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
        self._text_cursor = 0
        self._play_lock = asyncio.Lock()
        # Época de reproducción (issue #89). reset() tiene que ser síncrono
        # (state_machine lo llama sin await), así que no puede tomar
        # _play_lock. En su lugar incrementa la época: lo que estuviera
        # esperando el lock con una época anterior es del turno cancelado y
        # se descarta al entrar, sin depender de que el llamador haya
        # cancelado y esperado antes las tareas de reproducción.
        self._epoch = 0

    def push_token(self, content: str) -> None:
        if content:
            self._text_buffer.append(content)

    async def play_chunk(self, audio: bytes) -> None:
        if not audio:
            return
        # Un único evento por chunk (antes: un tick cada 50 ms, hasta ~20
        # publicaciones/s empujadas a cada display, issue #64). Se manda el
        # texto pendiente desde el cursor hasta el total acumulado junto con
        # la duración real del audio; el display anima el reveal localmente
        # (docs/display-protocol.md). Se publica ANTES de reproducir para que
        # el reveal empiece cuando empieza a sonar el chunk.
        audio_duration = max(len(audio) / (24000 * 2), 0.001)
        full_text = "".join(self._text_buffer)
        chunk_text = full_text[self._text_cursor:]
        self._text_cursor = len(full_text)
        self._bus.publish(VoiceEvent(type="display_text_update", data={
            "chunk_text": chunk_text,
            "cumulative_text": full_text,
            "duration_s": audio_duration,
        }))

        epoch = self._epoch
        async with self._play_lock:
            if epoch != self._epoch:
                log.debug("PlaybackEngine: chunk descartado (reset mientras esperaba)")
                return
            await self._audio.play_chunk(audio)

    async def play_notification(self) -> None:
        epoch = self._epoch
        async with self._play_lock:
            if epoch != self._epoch:
                log.debug("PlaybackEngine: notificación descartada (reset mientras esperaba)")
                return
            await self._audio.play_notification()

    async def drain(self) -> None:
        async with self._play_lock:
            await self._audio.drain()

    def reset(self) -> None:
        # El backend se limpia con su propio lock de hilo (SounddeviceBackend,
        # issue #88); el chunk que esté sonando ahora termina su espera sobre
        # una cola ya vacía. TermuxBackend solo toca su carry desde el loop y
        # sin await entre lectura y escritura, así que no necesita más.
        self._epoch += 1
        self._text_buffer.clear()
        self._text_cursor = 0
        self._audio.reset()

    def close(self) -> None:
        """No-op: el ciclo de vida del audio backend lo gestiona voice_client."""
