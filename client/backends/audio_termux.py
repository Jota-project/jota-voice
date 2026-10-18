"""TermuxBackend — captura con parec/PulseAudio + reproducción con PyAudio.

Específico para Android/Termux. En Mac/Linux usa SounddeviceBackend.
"""
from __future__ import annotations

import asyncio
import logging

from config import AudioConfig
from platforms.termux.audio_capture_termux import AudioCapture
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
        # Byte suelto arrastrado al siguiente chunk cuando la red entrega un
        # mensaje TTS de longitud impar: sin esto, PyAudio.Stream.write()
        # calcula frames como len(data) // 2 y descarta el último byte
        # silenciosamente, perdiendo 1 muestra por cada chunk impar.
        self._enqueue_carry: bytes = b""

    async def start(self) -> None:
        # pyaudio se comprueba ANTES de arrancar la captura: si faltaba, parec
        # y su hilo lector quedaban vivos sin nadie que los parase.
        pyaudio = _require_pyaudio()
        if not await self._capture.start():
            # Nos pararon durante el arranque: no inicializar PyAudio para
            # un backend que ya se está apagando.
            log.debug("TermuxBackend: captura parada durante el arranque")
            return
        try:
            self._pa = pyaudio.PyAudio()
        except BaseException:
            # Mismo caso con la captura ya en marcha: pararla antes de propagar.
            try:
                await self._capture.stop()
            except Exception as exc:
                log.warning("TermuxBackend.start: error parando la captura tras fallo: %s", exc)
            raise
        log.debug("TermuxBackend: PyAudio inicializado")

    async def stop(self) -> None:
        # Importante: este método debe adquirir self._lock igual que _play()
        # y drain(). Sin el lock, una coroutine de shutdown puede cerrar
        # self._stream / self._pa mientras _play() está dentro de
        # `async with self._lock:` en `run_in_executor(None, self._stream.write, audio)`,
        # provocando en PyAudio errores tipo "Stream closed" / "Invalid sample
        # rate" contra un handle ya cerrado. Regresión #29.
        self._enqueue_carry = b""
        async with self._lock:
            await self._capture.stop()
            # Igual que en drain() y en AudioCapture.stop(): las llamadas a
            # PortAudio, que pueden bloquear, van al executor y no al loop.
            loop = asyncio.get_running_loop()
            stream, self._stream = self._stream, None
            pa, self._pa = self._pa, None
            if stream is not None:
                try:
                    await loop.run_in_executor(None, stream.stop_stream)
                    await loop.run_in_executor(None, stream.close)
                except Exception:
                    pass
            if pa is not None:
                await loop.run_in_executor(None, pa.terminate)

    def get_queue(self) -> asyncio.Queue[bytes]:
        return self._capture.get_queue()

    def get_oww_queue(self) -> asyncio.Queue[bytes]:
        return self._capture.get_oww_queue()

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
        # El chunking de red no garantiza alinear cada mensaje TTS a un
        # número par de bytes (tamaño de una muestra int16): sin este
        # acarreo, PyAudio.Stream.write() calcularía frames como
        # len(data) // 2 y descartaría el último byte silenciosamente.
        # Pegamos el byte suelto al siguiente chunk en lugar de perderlo.
        audio = self._enqueue_carry + audio
        if len(audio) % 2 != 0:
            self._enqueue_carry = audio[-1:]
            audio = audio[:-1]
        else:
            self._enqueue_carry = b""
        if not audio:
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
        self._enqueue_carry = b""
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
        # No hay buffer interno de texto en TermuxBackend (lo mantiene PlaybackEngine),
        # pero sí el byte suelto arrastrado: si un turno se cancela a medias
        # (reset() entre turnos), reproducirlo al principio del siguiente
        # sería ruido audible sin contexto. Y también el preroll del
        # AudioCapture subyacente (revisión post-Fase A): audio previo al
        # cancel se colaría en la siguiente transcripción.
        self._enqueue_carry = b""
        self._capture.reset()