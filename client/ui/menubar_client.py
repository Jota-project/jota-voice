"""MenubarClient — suscriptor del EventBus que traduce VoiceEvent a
llamadas sobre un MenubarBackend inyectado, y drena una queue.Queue
thread-safe para los comandos que la UI envía hacia asyncio.
"""
from __future__ import annotations

import asyncio
import logging
import queue

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
    ) -> None:
        """Loop suscrito al bus. Se cancela externamente (asyncio.CancelledError).

        El bus publica eventos asyncio-safe; la ui_queue es thread-safe
        (queue.Queue) y la drenamos desde un executor para no bloquear el
        loop mientras la UI está inactiva.
        """
        loop = asyncio.get_running_loop()

        def _get_nowait_or_none():
            # Timeout corto para que el hilo del executor vuelva
            # periódicamente y la cancelación de _drain_queue pueda
            # entregarse: un ui_queue.get() sin timeout bloquearía el
            # hilo del executor para siempre si no llega ningún comando,
            # e impediría que `drain_task.cancel()` surta efecto (asyncio
            # no puede interrumpir una llamada bloqueante ya en curso).
            try:
                return ui_queue.get(timeout=0.2)
            except queue.Empty:
                return None

        async def _drain_queue() -> None:
            while True:
                cmd = await loop.run_in_executor(None, _get_nowait_or_none)
                if cmd is None:
                    continue
                if cmd == "toggle_pause":
                    if pause_event.is_set():
                        pause_event.clear()
                    else:
                        pause_event.set()
                    self._backend.set_listening_paused(pause_event.is_set())
                    log.info("MenubarClient: toggle_pause -> paused=%s", pause_event.is_set())
                elif cmd == "open_logs":
                    log.info("MenubarClient: open_logs requested")
                    # Acción manejada por el caller (voice_client.main); aquí
                    # sólo logueamos para mantener el cliente agnóstico.
                elif cmd == "open_config":
                    log.info("MenubarClient: open_config requested")
                elif cmd == "shutdown_service":
                    log.info("MenubarClient: shutdown_service requested")
                elif cmd == "quit":
                    log.info("MenubarClient: quit requested")
                else:
                    log.warning("MenubarClient: comando UI desconocido: %r", cmd)

        drain_task = asyncio.create_task(_drain_queue())

        try:
            async for event in bus.subscribe():
                await self._handle(event)
        finally:
            self._cancel_pending_idle()
            drain_task.cancel()
            try:
                await drain_task
            except asyncio.CancelledError:
                pass

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
            self._pending_idle_task = None
