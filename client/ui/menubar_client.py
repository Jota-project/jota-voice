"""MenubarClient — suscriptor del EventBus que traduce VoiceEvent a
llamadas sobre un MenubarBackend inyectado, y reenvía al loop de asyncio
los comandos que la UI encola en una queue.Queue thread-safe.
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import signal
import threading

from domain.event_bus import EventBus, VoiceEvent

from .menubar_base import (
    HEALTH_FATAL,
    HEALTH_INFO,
    HEALTH_OK,
    HEALTH_WARN,
    MenubarBackend,
)

log = logging.getLogger(__name__)


_HOLD_TRANSIENT_S_DEFAULT = 3.0

# --- Salud del sistema (issues #108 y #7) ---------------------------------
# Cada condición activa se guarda por "fuente"; el menubar muestra todas,
# las más graves primero. El orden de esta tupla desempata a igual gravedad.
_HEALTH_SOURCES = ("fatal", "gateway", "wake_word", "transcriber", "orchestrator", "tts")
_SEVERITY = {HEALTH_INFO: 1, HEALTH_WARN: 2, HEALTH_FATAL: 3}

# Servicios que el gateway reporta en {"type": "status", "service", "state"}.
_SERVICE_LABELS = {
    "transcriber": "Transcripción",
    "orchestrator": "Orquestador",
    "tts": "Síntesis de voz",
}
# Texto por estado de servicio.
_SERVICE_TEXTS = {
    "unavailable": (HEALTH_WARN, "{label} no disponible"),
    "reconnecting": (HEALTH_WARN, "{label}: reconectando…"),
}
# "degraded" es solo del transcriber y es una señal de CALIDAD, no de
# conectividad; además jota-gateway#227 la emite en reposo sin motivo, así
# que va como aviso informativo (sin icono ni contador de errores).
_SERVICE_DEGRADED_TEXT = {
    "transcriber": "Transcripción degradada",
    "orchestrator": "Orquestador degradado",
    "tts": "Síntesis de voz degradada",
}
# Evento de un turno correcto que demuestra que el servicio funciona y
# limpia su aviso aunque nunca llegue el "restored".
_SERVICE_CLEARED_BY = {
    "transcription": "transcriber",
    "llm_token": "orchestrator",
    "playback_started": "tts",
}
# Estados de la propia sesión (gateway_session._status).
_GATEWAY_TEXTS = {
    "reconnecting": "Gateway: reconectando…",
    "disconnected": "Gateway: desconectado",
}
_ORDERLY_CLOSE_CODES = (1000, 1001)
# kind de GatewayHandshakeError (backends.gateway_client.classify_handshake_close).
_HANDSHAKE_TEXTS = {
    "invalid_client_key": "Gateway: client_key inválida",
    "agent_not_permitted": "Gateway: agente no permitido",
    "agent_not_available": "Gateway: agente no disponible",
    "policy_rejected": "Gateway: conexión rechazada",
    "service_unavailable": "Gateway: servicio no disponible",
}
_MAX_DETAIL_CHARS = 60


def _shorten(text: str, limit: int = _MAX_DETAIL_CHARS) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class MenubarClient:
    def __init__(self, backend: MenubarBackend, hold_transient_s: float = _HOLD_TRANSIENT_S_DEFAULT) -> None:
        self._backend = backend
        self._errors_count = 0
        self._hold_transient_s = hold_transient_s
        self._hold_until: float = 0.0
        self._pending_idle_task: "asyncio.Task | None" = None
        self._health: dict[str, tuple[str, str]] = {}
        self._last_health: tuple[str, str] = (HEALTH_OK, "")

    async def run(
        self,
        bus: EventBus,
        ui_queue: queue.Queue,
        pause_event: asyncio.Event,
        loop: asyncio.AbstractEventLoop,
        stop_event: asyncio.Event,
    ) -> None:
        """Loop suscrito al bus. Termina al cerrarse el bus o al cancelarlo.

        Los comandos de la UI llegan por ``ui_queue`` (queue.Queue, la
        escribe el hilo de Cocoa). Un hilo daemon propio bloquea en
        ``ui_queue.get()`` y reenvía cada comando al loop con
        ``call_soon_threadsafe`` (issue #100): sin sondeo periódico y sin
        ocupar para siempre un hilo del executor por defecto, que comparten
        las operaciones bloqueantes de audio/HTTP. Al salir se encola un
        centinela que desbloquea y termina ese hilo.
        """
        self._loop = loop
        stop_sentinel = object()

        def _forward_commands() -> None:
            while True:
                cmd = ui_queue.get()
                if cmd is stop_sentinel:
                    return
                try:
                    loop.call_soon_threadsafe(self._dispatch_command, cmd, pause_event)
                except RuntimeError:
                    log.debug("MenubarClient: loop cerrado; comando %r descartado", cmd)
                    return

        forwarder = threading.Thread(
            target=_forward_commands, name="menubar-ui-commands", daemon=True
        )
        forwarder.start()

        try:
            async for event in bus.subscribe():
                await self._handle(event)
        finally:
            self._cancel_pending_idle()
            ui_queue.put_nowait(stop_sentinel)

    def _dispatch_command(self, cmd: str, pause_event: asyncio.Event) -> None:
        """Ejecuta en el loop de asyncio un comando que la UI encoló.

        Contrato de la cola (issue #99): por ui_queue pasan SOLO los
        comandos que necesitan tocar estado de asyncio o que no deben correr
        en el hilo de la UI. Hoy son dos:

        - ``"toggle_pause"``: conmuta ``pause_event`` (lo vigila oww_loop).
        - ``"shutdown_service"``: envía SIGTERM al propio proceso (#30).

        Abrir logs, abrir configuración y Salir son callbacks directos de
        MenubarCommands en voice_client (Popen no bloquea; Salir usa
        call_soon_threadsafe), así que no tienen rama aquí. Un comando nuevo
        solo necesita rama si cumple el criterio de arriba.
        """
        if cmd == "toggle_pause":
            if pause_event.is_set():
                pause_event.clear()
            else:
                pause_event.set()
            self._backend.set_listening_paused(pause_event.is_set())
            log.info("MenubarClient: toggle_pause -> paused=%s", pause_event.is_set())
        elif cmd == "shutdown_service":
            # El bug original (#30) era subprocess.run sincrónico en el hilo
            # Cocoa. launchctl disable/bootout en macOS Sonoma con
            # KeepAlive=true no desactiva el agente (launchd lo relanza en
            # ~1s), así que enviamos SIGTERM al proceso actual: el observer
            # NSFileHandle (issue #104) lo captura, stop_event se dispara,
            # main() apaga y NSApp.run() retorna. launchd puede relanzarlo
            # (KeepAlive=true); para desactivarlo del todo:
            # 'launchctl unload -w ~/Library/LaunchAgents/com.jota.voice.plist'.
            pid = os.getpid()
            log.info(
                "MenubarClient: shutdown_service received, enviando SIGTERM "
                "a PID %d (KeepAlive=true puede relanzar el proceso)", pid,
            )
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception as exc:
                log.warning("MenubarClient: os.kill(SIGTERM) falló: %s", exc)
        else:
            log.warning("MenubarClient: comando UI desconocido: %r", cmd)

    async def _handle(self, event: VoiceEvent) -> None:
        if not isinstance(event.data, dict):
            return

        self._update_health(event)

        if event.type == "state_changed":
            state = event.data.get("state", "")
            if state == "idle":
                self._set_idle_holding_transients()
            else:
                self._set_state_now(state)
        elif event.type == "wake_word_detected":
            # La state machine actual solo publica state_changed para
            # "idle" — nunca para "listening"/"thinking"/"speaking". El
            # icono debe reaccionar en el instante de la detección, así
            # que derivamos el estado visual directamente de los eventos
            # de dominio que sí se publican (ver domain/state_machine.py).
            self._set_state_now("listening")
        elif event.type == "recording_ended":
            self._set_state_now("thinking")
        elif event.type == "playback_started":
            self._set_state_now("speaking")
        elif event.type == "transcription":
            self._backend.set_status_text(event.data.get("text", ""))
        elif event.type == "error":
            self._errors_count += 1
            self._backend.set_errors_count(self._errors_count)
            self._set_transient_state("error")
        elif event.type == "cancelled":
            self._set_transient_state("cancelled")

    # ------------------------------------------------------------------
    # Salud: gateway, servicios del gateway y wake word (#108, #7)
    # ------------------------------------------------------------------

    def _update_health(self, event: VoiceEvent) -> None:
        data = event.data
        before = dict(self._health)

        if event.type == "gateway_status":
            service = data.get("service")
            state = data.get("state")
            if service == "gateway":
                self._on_session_status(state, data.get("code"))
            elif service in _SERVICE_LABELS:
                self._on_service_status(service, state)
        elif event.type == "gateway_ready":
            # Handshake completo: la sesión está viva y aceptada. No borra
            # los status de servicios: llegan ANTES del ready.
            self._health.pop("gateway", None)
            self._health.pop("fatal", None)
        elif event.type == "error":
            self._on_error(data)
        elif event.type == "oww_degraded":
            self._health["wake_word"] = (HEALTH_WARN, "Wake word no disponible")
        elif event.type == "oww_recovered":
            self._health.pop("wake_word", None)
        elif event.type in _SERVICE_CLEARED_BY:
            if event.type != "transcription" or str(data.get("text", "")).strip():
                self._health.pop(_SERVICE_CLEARED_BY[event.type], None)

        if self._health != before:
            self._push_health()

    def _on_session_status(self, state, close_code) -> None:
        if state == "connected":
            self._health.pop("gateway", None)
            self._health.pop("fatal", None)
            return
        if state == "disconnected":
            # Los status de servicios eran de la sesión que acaba de caer.
            for service in _SERVICE_LABELS:
                self._health.pop(service, None)
            if close_code in _ORDERLY_CLOSE_CODES:
                # Cierre ordenado (inactividad): el supervisor reabre al
                # instante sin "reconnecting". Si no lo consigue, llegarán
                # reconnecting o un error; avisar aquí solo haría parpadear
                # el icono.
                return
        if state in _GATEWAY_TEXTS:
            self._health["gateway"] = (HEALTH_WARN, _GATEWAY_TEXTS[state])

    def _on_service_status(self, service: str, state) -> None:
        if state == "restored":
            self._health.pop(service, None)
        elif state == "degraded":
            self._health[service] = (HEALTH_INFO, _SERVICE_DEGRADED_TEXT[service])
        elif state in _SERVICE_TEXTS:
            level, template = _SERVICE_TEXTS[state]
            self._health[service] = (level, template.format(label=_SERVICE_LABELS[service]))
        else:
            log.debug("MenubarClient: status %r de %s ignorado", state, service)

    def _on_error(self, data: dict) -> None:
        code = data.get("code")
        if data.get("fatal") is True:
            # Un rechazo fatal (client_key inválida, agente no permitido…) no
            # se reintenta solo: se queda visible hasta que una sesión vuelva
            # a abrirse (gateway_ready / connected).
            if code == "GATEWAY_HANDSHAKE_REJECTED":
                text = _HANDSHAKE_TEXTS.get(data.get("kind"), "Gateway: conexión rechazada")
            else:
                text = f"Error grave: {_shorten(data.get('message') or code or 'desconocido')}"
            self._health["fatal"] = (HEALTH_FATAL, text)
        elif code == "GATEWAY_DISCONNECTED" and "gateway" not in self._health:
            self._health["gateway"] = (HEALTH_WARN, "Gateway: sin conexión")

    def _push_health(self) -> None:
        entries = [
            (_SEVERITY[level], -_HEALTH_SOURCES.index(source), level, text)
            for source, (level, text) in self._health.items()
        ]
        if entries:
            entries.sort(reverse=True)
            health = (entries[0][2], " · ".join(e[3] for e in entries))
        else:
            health = (HEALTH_OK, "")
        if health != self._last_health:
            self._last_health = health
            self._backend.set_health(*health)

    def _set_state_now(self, state: str) -> None:
        """Aplica un estado inmediatamente y cancela cualquier reversión a
        "idle" pendiente — un evento real (nueva detección, nueva
        grabación...) siempre prevalece sobre un icono de error/cancelled
        que estuviera reteniéndose."""
        self._cancel_pending_idle()
        self._backend.set_state(state)

    def _set_transient_state(self, state: str) -> None:
        """Como _set_state_now, pero además abre una ventana de "hold":
        el próximo state_changed("idle") no se aplica hasta que pase, así
        el icono no se sobreescribe antes de que el usuario llegue a verlo
        (_idle() publica "idle" en la iteración siguiente del loop, casi
        instantáneamente tras el error/cancelled)."""
        self._set_state_now(state)
        self._hold_until = asyncio.get_running_loop().time() + self._hold_transient_s

    def _cancel_pending_idle(self) -> None:
        if self._pending_idle_task is not None:
            self._pending_idle_task.cancel()
            self._pending_idle_task = None

    def _set_idle_holding_transients(self) -> None:
        remaining = self._hold_until - asyncio.get_running_loop().time()
        if remaining <= 0:
            self._backend.set_state("idle")
            return
        self._cancel_pending_idle()
        self._pending_idle_task = asyncio.create_task(self._delayed_idle(remaining))

    async def _delayed_idle(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            self._backend.set_state("idle")
        finally:
            if self._pending_idle_task is asyncio.current_task():
                self._pending_idle_task = None
