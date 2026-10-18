"""TermuxBackend — captura con parec/PulseAudio + reproducción con PyAudio.

Específico para Android/Termux. En Mac/Linux usa SounddeviceBackend.
"""
from __future__ import annotations

import asyncio
import functools
import logging

from config import AudioConfig
from core.audio.offloop import call_off_loop, start_off_loop
from platforms.termux.audio_capture_termux import AudioCapture
from .notification_tone import synth_notification_tone

log = logging.getLogger(__name__)

_TTS_SAMPLE_RATE = 24000  # PCM16 mono del gateway

# Tope por cada llamada de cierre a PyAudio/PortAudio (stop_stream, close,
# terminate). Pasado este tiempo el driver se da por colgado y el recurso se
# abandona: volver a tocarlo desde otro hilo no es seguro.
_STREAM_CLOSE_TIMEOUT_S = 2.0

# Tope de espera a un write() que sigue en curso tras cancelar _play(). Un
# write bloquea lo que dura su chunk (normalmente < 1 s); si tarda más, el
# driver se da por colgado y el stream se abandona.
_WRITE_WAIT_TIMEOUT_S = 3.0


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
        # write() en curso en su hilo. Cancelar _play() no lo interrumpe, así
        # que antes de volver a tocar el stream (otro write, drain, stop) hay
        # que esperar a que termine.
        self._write_fut: asyncio.Future | None = None

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
            # Las llamadas a PortAudio, que pueden bloquear, van fuera del
            # loop en hilos daemon con tope (B4): el executor por defecto usa
            # hilos no daemon que retendrían la salida con el driver colgado.
            stream, self._stream = self._stream, None
            pa, self._pa = self._pa, None
            if stream is not None and not await self._wait_inflight_write("stop"):
                return  # write colgado: ni close() ni terminate() concurrentes
            if stream is not None and not await self._close_stream(stream, "stop"):
                # Stream abandonado: terminate() tocaría PortAudio en
                # paralelo con el hilo colgado. Se deja para el SO.
                return
            if pa is not None:
                await self._off_loop(pa.terminate, "stop terminate")

    @staticmethod
    async def _off_loop(fn, label: str) -> bool:
        """Ejecuta una llamada de cierre fuera del loop. Devuelve False solo si
        agotó el tiempo (driver colgado); un error normal se loguea."""
        try:
            await call_off_loop(fn, _STREAM_CLOSE_TIMEOUT_S, name="termux-audio-close")
        except (TimeoutError, asyncio.TimeoutError):
            log.error(
                "TermuxBackend.%s: timeout (%.1fs), el driver no responde; se abandona",
                label, _STREAM_CLOSE_TIMEOUT_S,
            )
            return False
        except Exception as exc:
            log.warning("TermuxBackend.%s: error cerrando stream: %s", label, exc)
        return True

    async def _close_stream(self, stream, label: str) -> bool:
        """stop_stream() + close(), cada uno con su try: si stop_stream()
        falla, close() se intenta igual. Si alguno se cuelga, el stream se
        abandona sin más llamadas y devuelve False."""
        if not await self._off_loop(stream.stop_stream, f"{label} stop_stream"):
            return False
        return await self._off_loop(stream.close, f"{label} close")

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
            if not await self._wait_inflight_write("play"):
                # El write anterior sigue colgado: el stream es inservible.
                self._stream = None
                return
            fut = start_off_loop(
                functools.partial(self._stream.write, audio), name="termux-audio-write"
            )
            self._write_fut = fut
            # shield: si cancelan esta tarea, el write sigue en su hilo y
            # _write_fut permite al siguiente esperar a que acabe.
            await asyncio.shield(fut)

    async def _wait_inflight_write(self, label: str) -> bool:
        """Espera (acotado) al write() en curso de un _play() cancelado.
        Devuelve False si no terminó a tiempo: hay que abandonar el stream
        sin más llamadas, porque PortAudio no admite llamadas concurrentes."""
        fut = self._write_fut
        if fut is None or fut.done():
            return True
        await asyncio.wait({fut}, timeout=_WRITE_WAIT_TIMEOUT_S)
        if not fut.done():
            log.error(
                "TermuxBackend.%s: el write() en curso no terminó en %.1fs; "
                "se abandona el stream", label, _WRITE_WAIT_TIMEOUT_S,
            )
            return False
        self._write_fut = None
        return True

    async def drain(self) -> None:
        self._enqueue_carry = b""
        async with self._lock:
            stream, self._stream = self._stream, None
            if stream is not None and await self._wait_inflight_write("drain"):
                await self._close_stream(stream, "drain")

    def reset(self) -> None:
        # No hay buffer interno de texto en TermuxBackend (lo mantiene PlaybackEngine),
        # pero sí el byte suelto arrastrado: si un turno se cancela a medias
        # (reset() entre turnos), reproducirlo al principio del siguiente
        # sería ruido audible sin contexto. Y también el preroll del
        # AudioCapture subyacente (revisión post-Fase A): audio previo al
        # cancel se colaría en la siguiente transcripción.
        self._enqueue_carry = b""
        self._capture.reset()