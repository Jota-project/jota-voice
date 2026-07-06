"""PlaybackEngine — orquesta el playback delegando en AudioBackend.

Responsabilidades:
- Acumular tokens de LLM en texto completo (para display sincronizado)
- Delegar play_chunk / play_notification / drain en el AudioBackend inyectado

El motor de audio real vive en client/backends/ (sounddevice o pyaudio según SO).
"""
from __future__ import annotations

import asyncio
import logging

from domain.event_bus import EventBus, VoiceEvent

log = logging.getLogger(__name__)


class PlaybackEngine:
    """Orquestador de reproducción. No accede directamente a hardware."""

    def __init__(self, bus: EventBus, audio) -> None:
        self._bus = bus
        self._audio = audio
        self._text_buffer: list[str] = []
        self._text_cursor: float = 0.0
        self._play_lock = asyncio.Lock()

    def push_token(self, content: str) -> None:
        if content:
            self._text_buffer.append(content)

    async def play_chunk(self, audio: bytes) -> None:
        if not audio:
            return
        # Cálculo simplificado de velocidad de texto (chars/segundo) para
        # sincronizar el cursor visible con la duración del audio. Conservamos
        # la misma heurística que la versión anterior (audio_duration / 0.05 ticks).
        audio_duration = max(len(audio) / (24000 * 2), 0.001)
        tick = 0.05
        n_ticks = max(1, round(audio_duration / tick))

        total_chars = sum(len(t) for t in self._text_buffer)
        pending_chars = total_chars - int(self._text_cursor)
        chars_per_second = (
            pending_chars / audio_duration
            if pending_chars > 0 else 0.0
        )
        full_text = "".join(self._text_buffer)

        async def _animate_text() -> None:
            for i in range(n_ticks):
                await asyncio.sleep(tick)
                if chars_per_second > 0:
                    self._text_cursor = min(
                        self._text_cursor + chars_per_second * tick,
                        float(total_chars),
                    )
                visible = full_text[: int(self._text_cursor)]
                self._bus.publish(
                    VoiceEvent(type="display_text_update", data={"text": visible})
                )

        async with self._play_lock:
            # El backend ya bloquea la duración real del audio (escritura a
            # hardware o sleep interno); animar el texto en paralelo evita
            # esperar la misma duración dos veces.
            await asyncio.gather(self._audio.play_chunk(audio), _animate_text())

    async def play_notification(self) -> None:
        async with self._play_lock:
            await self._audio.play_notification()

    async def drain(self) -> None:
        async with self._play_lock:
            await self._audio.drain()

    def reset(self) -> None:
        self._text_buffer.clear()
        self._text_cursor = 0.0
        self._audio.reset()

    def close(self) -> None:
        """No-op: el ciclo de vida del audio backend lo gestiona voice_client."""
