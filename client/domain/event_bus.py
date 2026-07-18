"""
event_bus.py — EventBus y VoiceEvent para jota-voice v2.

Sin dependencias externas: solo stdlib + asyncio.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator, AsyncIterator, Literal


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
        "display_text_update",  # emitido por PlaybackEngine, consumido por DisplayClient
        "error",
        "cancelled",
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

    async def subscribe(self) -> AsyncGenerator[VoiceEvent, None]:
        """
        Devuelve un iterador async independiente que recibe todos los eventos
        publicados desde el momento de la suscripción.

        Uso:
            async for event in bus.subscribe():
                handle(event)

        Para detener la iteración llama a bus.close() o usa una tarea cancelable.
        """
        q: asyncio.Queue[VoiceEvent | None] = asyncio.Queue()
        self._queues.append(q)
        try:
            while True:
                event = await q.get()
                if event is None:
                    # sentinel de cierre
                    break
                yield event
        finally:
            self._queues.remove(q)

    def close(self) -> None:
        """Envía sentinel None a todos los suscriptores para que terminen limpiamente."""
        for q in self._queues:
            q.put_nowait(None)
