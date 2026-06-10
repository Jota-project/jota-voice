"""
playback_engine.py — PlaybackEngine para jota-voice v2.

Responsabilidades:
- Recibir chunks de audio TTS (PCM16, mono, 24kHz) y reproducirlos via PyAudio.
- Acumular tokens LLM en un text_buffer y avanzar un cursor de texto
  sincronizado con la duración del audio.
- Emitir VoiceEvent(type="display_text_update") cada ~50ms durante la reproducción.

Dependencias: pyaudio (solo en hardware Termux/ARM), asyncio, event_bus.
"""

from __future__ import annotations

import asyncio
import logging

import pyaudio

from client.event_bus import EventBus, VoiceEvent

log = logging.getLogger(__name__)

# Parámetros fijos del stream TTS
_SAMPLE_RATE = 24000
_SAMPLE_WIDTH = 2  # PCM16 → 2 bytes por muestra
_TICK = 0.05       # intervalo de actualización de texto (50 ms)


class PlaybackEngine:
    """
    Motor de reproducción de audio TTS con sincronización texto/audio.

    Parámetros
    ----------
    bus : EventBus
        Bus de eventos donde se publican los display_text_update.
    pa : pyaudio.PyAudio
        Instancia compartida de PyAudio (el caller gestiona su ciclo de vida).
    """

    def __init__(self, bus: EventBus, pa: pyaudio.PyAudio) -> None:
        self._bus = bus
        self._pa = pa
        self._stream: pyaudio.Stream | None = None

        # Estado de texto
        self._text_buffer: list[str] = []
        self._text_cursor: float = 0.0
        self._play_lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def push_token(self, content: str) -> None:
        """Añade un token LLM al buffer de texto."""
        if content:
            self._text_buffer.append(content)

    async def play_chunk(self, audio: bytes) -> None:
        """
        Reproduce un chunk PCM16 24kHz y avanza el cursor de texto de forma
        sincronizada, emitiendo display_text_update cada ~50ms.
        """
        if not audio:
            return

        async with self._play_lock:
            # Asegurar que el stream está abierto
            self._ensure_stream()

            audio_duration = len(audio) / (_SAMPLE_RATE * _SAMPLE_WIDTH)

            # Lanzar la escritura de audio como tarea concurrente
            loop = asyncio.get_running_loop()
            write_task = loop.run_in_executor(None, self._stream.write, audio)

            # Loop de ticks de texto anclado al tiempo real
            t0 = loop.time()
            while True:
                await asyncio.sleep(_TICK)
                real_elapsed = loop.time() - t0
                if real_elapsed >= audio_duration:
                    break
                # Recalcular chars/s en cada tick (tokens nuevos pueden llegar mid-chunk)
                total_chars = sum(len(t) for t in self._text_buffer)
                pending_chars = total_chars - int(self._text_cursor)
                remaining_time = max(audio_duration - real_elapsed, _TICK)
                cps = pending_chars / remaining_time if pending_chars > 0 else 0.0
                if cps > 0:
                    self._text_cursor = min(
                        self._text_cursor + cps * _TICK,
                        float(total_chars),
                    )
                visible = "".join(self._text_buffer)[: int(self._text_cursor)]
                self._bus.publish(
                    VoiceEvent(type="display_text_update", data={"text": visible})
                )

            # Esperar que el write termine antes de retornar
            await write_task

    async def drain(self) -> None:
        """
        Espera el fin de reproducción del último chunk.

        Con el diseño actual, play_chunk ya espera internamente que el write
        termine antes de retornar, por lo que drain() es un no-op salvo que
        se necesite extender en el futuro.
        """
        pass

    def reset(self) -> None:
        """Limpia el estado de texto entre turnos de conversación."""
        self._text_buffer.clear()
        self._text_cursor = 0.0

    def close(self) -> None:
        """Cierra el stream PyAudio si está abierto."""
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception as exc:
                log.warning("PlaybackEngine.close(): error al cerrar stream: %s", exc)
            finally:
                self._stream = None

    # ------------------------------------------------------------------
    # Interno
    # ------------------------------------------------------------------

    def _ensure_stream(self) -> None:
        """Abre el stream de reproducción PyAudio si aún no está abierto."""
        if self._stream is None:
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=_SAMPLE_RATE,
                output=True,
            )
