"""PlaybackEngine — orquesta el playback delegando en AudioBackend.

Responsabilidades:
- Acumular tokens de LLM en texto completo (para display sincronizado)
- Delegar play_chunk / play_notification / drain en el AudioBackend inyectado

El motor de audio real vive en client/backends/ (sounddevice o pyaudio según SO).
"""
from __future__ import annotations

import asyncio
import logging
import math
import re

from domain.event_bus import EventBus, VoiceEvent

log = logging.getLogger(__name__)

# Ritmo de habla estimado para repartir el texto entre los chunks de audio
# (caracteres por segundo, espacios incluidos; ~14-16 en español con el TTS
# del gateway). Prueba real 2026-10-21: con OpenClaw el gateway entrega todo
# el texto de la respuesta antes que el audio y el primer chunk (0,5 s) se
# llevaba los 600 caracteres; los 25 siguientes salían con chunk_text vacío.
CHARS_PER_SECOND = 15.0

_WHITESPACE = re.compile(r"\s")


class PlaybackEngine:
    """Orquestador de reproducción. No accede directamente a hardware."""

    def __init__(
        self, bus: EventBus, audio, *, chars_per_second: float = CHARS_PER_SECOND
    ) -> None:
        self._bus = bus
        self._audio = audio
        if chars_per_second <= 0:
            raise ValueError("chars_per_second debe ser > 0")
        self.chars_per_second = chars_per_second
        # Tokens recibidos desde el último chunk publicado, y texto completo
        # del turno ya publicado (issue #95: antes se hacía "".join() de
        # todos los tokens del turno en cada chunk).
        self._text_buffer: list[str] = []
        self._full_text = ""
        # Texto ya unido pero aún no publicado (no cupo en los chunks
        # anteriores) y crédito de caracteres. El crédito puede quedar
        # negativo: terminar la palabra en curso adelanta un poco el texto y
        # ese exceso se descuenta de los chunks siguientes, así el display no
        # se va adelantando a la voz chunk a chunk.
        self._pending = ""
        self._credit = 0.0
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
        self._publish_text(self._take_text(audio_duration), audio_duration)

        epoch = self._epoch
        async with self._play_lock:
            if epoch != self._epoch:
                log.debug("PlaybackEngine: chunk descartado (reset mientras esperaba)")
                return
            await self._audio.play_chunk(audio)

    def _take_text(self, audio_duration: float) -> str:
        """Saca del texto pendiente lo que corresponde a ``audio_duration``.

        - Si todo lo pendiente cabe en el crédito (tokens en streaming más
          lentos que la voz), sale entero, como antes, y el crédito sobrante
          se descarta: un rato de audio sin texto no debe adelantar después
          el texto de golpe.
        - Si no cabe (el gateway mandó el texto antes que el audio), se corta
          al final de la palabra en la que se agota el crédito, sin partir
          palabras, y el resto pasa al chunk siguiente.
        """
        # Solo se unen los tokens nuevos (issue #95). cumulative_text sigue
        # costando O(longitud del turno) por evento porque el protocolo del
        # display manda el texto completo en cada uno
        # (docs/display-protocol.md): ese coste es del protocolo.
        if self._text_buffer:
            self._pending += "".join(self._text_buffer)
            self._text_buffer.clear()
        self._credit += audio_duration * self.chars_per_second
        budget = math.floor(self._credit)
        if budget <= 0 or not self._pending:
            if not self._pending:
                self._credit = min(self._credit, 0.0)
            return ""
        if len(self._pending) <= budget:
            text, self._pending = self._pending, ""
            self._credit = 0.0
            return text
        # Fin de la palabra en la que se agota el crédito (el espacio se
        # incluye). Una palabra más larga que el crédito sale entera.
        m = _WHITESPACE.search(self._pending, budget - 1)
        cut = m.end() if m else len(self._pending)
        text, self._pending = self._pending[:cut], self._pending[cut:]
        self._credit -= cut
        return text

    def _publish_text(self, chunk_text: str, duration_s: float) -> None:
        self._full_text += chunk_text
        self._bus.publish(VoiceEvent(type="display_text_update", data={
            "chunk_text": chunk_text,
            "cumulative_text": self._full_text,
            "duration_s": duration_s,
        }))

    def _flush_text(self) -> None:
        """Publica de golpe el texto que quede (fin de turno).

        Con ``duration_s`` 0 el display lo pinta sin animar: si el ritmo
        estimado fue más lento que la voz real, el display no se queda corto.
        """
        if self._text_buffer:
            self._pending += "".join(self._text_buffer)
            self._text_buffer.clear()
        if self._pending:
            text, self._pending = self._pending, ""
            self._credit = 0.0
            self._publish_text(text, 0.0)

    async def play_notification(self) -> None:
        epoch = self._epoch
        async with self._play_lock:
            if epoch != self._epoch:
                log.debug("PlaybackEngine: notificación descartada (reset mientras esperaba)")
                return
            await self._audio.play_notification()

    async def drain(self) -> None:
        # Fin de turno: el texto que no cupo en los chunks sale ahora, antes
        # de esperar al backend (síncrono: sin carrera con reset()), así que
        # aparece mientras suena el último audio.
        self._flush_text()
        async with self._play_lock:
            await self._audio.drain()

    def reset(self) -> None:
        # La época solo evita que suene lo que esperaba el lock. Lo que ya
        # está sonando depende del backend:
        # - SounddeviceBackend vacía su cola con su lock de hilo (issue #88);
        #   el chunk en curso termina su espera sobre una cola ya vacía.
        # - TermuxBackend.reset() solo descarta el carry y el preroll: el
        #   write() bloqueante de PyAudio que esté en curso NO se corta y
        #   suena entero (hasta un chunk). Su drain()/stop() esperan, con
        #   tope, a que ese write termine antes de cerrar el stream.
        self._epoch += 1
        self._text_buffer.clear()
        self._full_text = ""
        self._pending = ""
        self._credit = 0.0
        self._audio.reset()

    def close(self) -> None:
        """No-op: el ciclo de vida del audio backend lo gestiona voice_client."""
