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

from .menubar_base import MenubarBackend

log = logging.getLogger(__name__)


_HOLD_TRANSIENT_S_DEFAULT = 3.0


class MenubarClient:
    def __init__(self, backend: MenubarBackend, hold_transient_s: float = _HOLD_TRANSIENT_S_DEFAULT) -> None:
        self._backend = backend
        self._errors_count = 0
        self._hold_transient_s = hold_transient_s
        self._hold_until: float = 0.0
        self._pending_idle_task: "asyncio.Task | None" = None

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
