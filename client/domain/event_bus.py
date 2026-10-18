"""
event_bus.py — EventBus y VoiceEvent para jota-voice v2.

Sin dependencias externas: solo stdlib + asyncio.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoiceEvent:
    type: Literal[
        # Ciclo de vida
        "wake_word_detected",
        "recording_started",
        "recording_ended",
        # Pipeline de respuesta
        "transcription_partial",
        "transcription",
        "llm_token",
        "tts_chunk",
        # Reproducción
        "playback_started",
        "playback_ended",
        # Sistema
        "state_changed",
        # Emitido por PlaybackEngine, uno por chunk de TTS: chunk_text,
        # cumulative_text, duration_s. Consumido por DisplayClient.
        "display_text_update",
        # Emitido por el gateway mid-turno (degradación de
        # orchestrator/transcriber/tts). Estado informativo, el turno
        # continúa — ver issue #18.
        "gateway_status",
        # Issue #24: sesión persistente — gateway_ready se publica al
        # arrancar, los tres siguientes durante IDLE por el listener.
        "gateway_ready",
        "interrupted",
        "turn_start",
        "tool_call",
        "error",
        "cancelled",
        # Issue #22: salud del wake word (OWWClient). DisplayClient los
        # reenvía al display como `health` (parte display de #7).
        "oww_degraded",
        "oww_recovered",
    ]
    data: dict
    ts: float = field(default_factory=time.monotonic)


class EventBus:
    """
    Bus de eventos asíncrono con soporte para múltiples suscriptores.

    Cada llamada a subscribe() registra una asyncio.Queue independiente.
    publish() inserta el evento en todas las colas (put_nowait, no bloquea).
    Los suscriptores lentos no bloquean a los demás ni al publicador.
    """

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue[VoiceEvent | None]] = []

    def publish(self, event: VoiceEvent) -> None:
        """Publica un evento en todas las colas de suscriptores registrados."""
        for q in self._queues:
            q.put_nowait(event)

    def subscribe(self) -> "_Subscription":
        """
        Devuelve un iterador async independiente que recibe todos los eventos
        publicados desde el momento de la LLAMADA (no desde la primera
        iteración): así un consumidor puede suscribirse antes de que arranque
        su task y no pierde lo publicado mientras tanto (p.ej. los `status`
        previos a `ready` del connect inicial).

        Uso:
            async for event in bus.subscribe():
                handle(event)

        Para detener la iteración llama a bus.close() o usa una tarea
        cancelable; ``aclose()`` retira la suscripción aunque no se haya
        llegado a iterar.
        """
        q: asyncio.Queue[VoiceEvent | None] = asyncio.Queue()
        self._queues.append(q)
        return _Subscription(self, q)

    def _unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    async def _iterate(self, q: asyncio.Queue) -> AsyncGenerator[VoiceEvent, None]:
        try:
            while True:
                event = await q.get()
                if event is None:
                    # sentinel de cierre
                    break
                yield event
        finally:
            self._unsubscribe(q)

    def close(self) -> None:
        """Envía sentinel None a todos los suscriptores para que terminen limpiamente."""
        for q in self._queues:
            q.put_nowait(None)


class _Subscription:
    """Iterador async de una suscripción ya registrada en el bus.

    Envuelve el generador ``EventBus._iterate`` (que retira la cola en su
    ``finally`` al terminar, cerrarse o recogerse) y añade que ``aclose()``
    la retire también si nunca se llegó a iterar.
    """

    def __init__(self, bus: EventBus, q: asyncio.Queue) -> None:
        self._bus = bus
        self._q = q
        self._gen = bus._iterate(q)

    def __aiter__(self) -> "_Subscription":
        return self

    async def __anext__(self) -> VoiceEvent:
        return await self._gen.__anext__()

    async def aclose(self) -> None:
        await self._gen.aclose()
        self._bus._unsubscribe(self._q)


async def consume(
    events: "_Subscription",
    handler: Callable[[VoiceEvent], Awaitable[None] | None],
    *,
    name: str,
) -> None:
    """Bucle estándar de un suscriptor del bus (issue #63).

    Llama a ``handler(event)`` (síncrono o async) por cada evento. Si lanza,
    se registra con el nombre del suscriptor y el bucle sigue: un backend
    defectuoso no puede matar la task de ese suscriptor para el resto de la
    sesión. ``publish`` ya aísla a los suscriptores entre sí (una cola por
    suscriptor, put_nowait). ``CancelledError`` se propaga. Termina al
    cerrarse el bus y, con ``aclosing``, retira la suscripción en el acto al
    terminar o cancelarse (si no, una suscripción creada de antemano, como
    las de voice_client, seguiría registrada acumulando eventos).
    """
    async with contextlib.aclosing(events):
        async for event in events:
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.exception("EventBus: el suscriptor %s falló procesando %s", name, event.type)
