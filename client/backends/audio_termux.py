"""TermuxBackend — captura con parec/PulseAudio + reproducción con PyAudio.

Específico para Android/Termux. En Mac/Linux usa SounddeviceBackend.
"""
from __future__ import annotations

import asyncio
import logging

from config import AudioConfig
from .audio_capture import AudioCapture
from .notification_tone import synth_notification_tone

log = logging.getLogger(__name__)

_TTS_SAMPLE_RATE = 24000  # PCM16 mono del gateway


def _require_pyaudio():
    """Import lazy de pyaudio — solo disponible en Termux/ARM."""
    try:
        import pyaudio
    except ImportError as exc:
        raise RuntimeError(
            "TermuxBackend requiere pyaudio. Instálalo con: pkg install portaudio && pip install pyaudio"
        ) from exc
    return pyaudio


class TermuxBackend:
    """AudioBackend que envuelve AudioCapture (parec + PulseAudio) y un mini-engine PyAudio."""

    def __init__(self, cfg: AudioConfig) -> None:
        self._cfg = cfg
        self._capture = AudioCapture(cfg)
        self._pa = None
        self._stream = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        await self._capture.start()
        pyaudio = _require_pyaudio()
        self._pa = pyaudio.PyAudio()
        log.debug("TermuxBackend: PyAudio inicializado")

    async def stop(self) -> None:
        await self._capture.stop()
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None

    def get_queue(self) -> asyncio.Queue[bytes]:
        return self._capture.get_queue()

    def get_preroll(self) -> bytes:
        return self._capture.get_preroll()

    def is_silence(self, frame: bytes) -> bool:
        return self._capture.is_silence(frame)

    async def play_notification(self) -> None:
        """Beep de notificación (dos tonos ascendentes, ~300ms total)."""
        wave = synth_notification_tone(_TTS_SAMPLE_RATE)
        await self._play(wave)

    async def play_chunk(self, audio: bytes) -> None:
        await self._play(audio)

    async def _play(self, audio: bytes) -> None:
        if not audio or self._pa is None:
            return
        async with self._lock:
            if self._stream is None:
                pyaudio = _require_pyaudio()
                self._stream = self._pa.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=_TTS_SAMPLE_RATE,
                    output=True,
                )
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._stream.write, audio)

    async def drain(self) -> None:
        async with self._lock:
            if self._stream is not None:
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self._stream.stop_stream)
                    await loop.run_in_executor(None, self._stream.close)
                except Exception as exc:
                    log.warning("TermuxBackend.drain: error cerrando stream: %s", exc)
                finally:
                    self._stream = None

    def reset(self) -> None:
        # No hay buffer interno de texto en TermuxBackend (lo mantiene PlaybackEngine).
        pass