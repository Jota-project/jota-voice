"""SounddeviceBackend — captura y reproducción con sounddevice (PortAudio).

Plataformas: macOS (CoreAudio), Linux (ALSA/PulseAudio). No funciona en Termux
(Android) — para eso usar TermuxBackend.
"""
from __future__ import annotations

import asyncio
import logging
import queue as queue_mod
import threading

import numpy as np

from config import AudioConfig
from core.audio.preroll import PrerollBuffer
from core.audio.vad import is_silence as _is_silence
from .notification_tone import synth_notification_tone

log = logging.getLogger(__name__)

_TTS_SAMPLE_RATE = 24000  # PCM16 mono del gateway


class SounddeviceBackend:
    """AudioBackend multiplataforma basado en sounddevice.

    Captura en `frames_per_buffer` chunks float32; VAD RMS; pre-roll ring-buffer.
    Reproduce chunks TTS int16 via OutputStream callback que lee de una queue.
    """

    def __init__(self, cfg: AudioConfig) -> None:
        self._cfg = cfg
        self._loop: asyncio.AbstractEventLoop | None = None
        self._capture_q: asyncio.Queue[bytes] | None = None
        self._oww_q: asyncio.Queue[bytes] | None = None
        self._input_stream = None
        self._output_stream = None
        self._play_q: queue_mod.Queue[bytes] = queue_mod.Queue()
        self._play_leftover: bytes | None = None
        self._enqueue_carry: bytes = b""
        self._capture_thread: threading.Thread | None = None
        self._capture_stop = threading.Event()

        self._preroll = PrerollBuffer(
            seconds=cfg.preroll_seconds,
            sample_rate=cfg.sample_rate,
            frames_per_buffer=cfg.frames_per_buffer,
        )
        self._lock = threading.Lock()

    # --- captura ---

    async def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "SounddeviceBackend requiere sounddevice. pip install sounddevice"
            ) from exc

        self._loop = asyncio.get_running_loop()
        self._capture_q = asyncio.Queue()
        self._oww_q = asyncio.Queue()
        self._capture_stop.clear()

        def _callback(indata, frames, time, status):  # noqa: ARG001
            if status:
                log.warning("SounddeviceBackend capture status: %s", status)
            self._on_frame(bytes(indata))  # float32 contiguo

        kwargs = dict(
            samplerate=self._cfg.sample_rate,
            channels=self._cfg.channels,
            dtype="float32",
            blocksize=self._cfg.frames_per_buffer,
            callback=_callback,
        )
        if self._cfg.input_device is not None:
            kwargs["device"] = self._cfg.input_device

        self._input_stream = sd.InputStream(**kwargs)
        self._input_stream.start()
        log.info("SounddeviceBackend: captura abierta (%d Hz, %d ch, block=%d)",
                 self._cfg.sample_rate, self._cfg.channels, self._cfg.frames_per_buffer)

    async def stop(self) -> None:
        self._capture_stop.set()
        if self._input_stream is not None:
            try:
                self._input_stream.stop()
                self._input_stream.close()
            except Exception as exc:
                log.warning("SounddeviceBackend.stop input: %s", exc)
            self._input_stream = None
        if self._output_stream is not None:
            try:
                self._output_stream.stop()
                self._output_stream.close()
            except Exception as exc:
                log.warning("SounddeviceBackend.stop output: %s", exc)
            self._output_stream = None
        # Drenar play queue
        self._play_leftover = None
        self._enqueue_carry = b""
        while not self._play_q.empty():
            try:
                self._play_q.get_nowait()
            except queue_mod.Empty:
                break

    # --- acceso a datos ---

    def _on_frame(self, chunk: bytes) -> None:
        """Punto único de entrada de cada frame capturado — hace fan-out a
        las dos colas independientes (RECORDING y OWW, ver get_queue() /
        get_oww_queue()) para que ningún consumidor le robe frames al otro
        (issue #9: antes ambos hacían `await q.get()` sobre la misma cola)."""
        with self._lock:
            self._preroll.append(chunk)
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._capture_q.put_nowait, chunk)
        self._loop.call_soon_threadsafe(self._oww_q.put_nowait, chunk)

    def get_queue(self) -> asyncio.Queue[bytes]:
        if self._capture_q is None:
            raise RuntimeError("SounddeviceBackend.get_queue() antes de start()")
        return self._capture_q

    def get_oww_queue(self) -> asyncio.Queue[bytes]:
        if self._oww_q is None:
            raise RuntimeError("SounddeviceBackend.get_oww_queue() antes de start()")
        return self._oww_q

    def get_preroll(self) -> bytes:
        with self._lock:
            return self._preroll.get()

    def is_silence(self, frame: bytes) -> bool:
        return _is_silence(frame, threshold_rms=self._cfg.vad_rms_threshold / 32768.0)

    # --- reproducción ---

    def _ensure_output_stream(self) -> None:
        if self._output_stream is not None:
            return
        import sounddevice as sd

        out_kwargs = dict(
            samplerate=_TTS_SAMPLE_RATE,
            channels=1,
            dtype="int16",
        )
        if self._cfg.output_device is not None:
            out_kwargs["device"] = self._cfg.output_device

        self._output_stream = sd.OutputStream(**out_kwargs, callback=self._out_callback)
        self._output_stream.start()

    def _out_callback(self, outdata, frames, time, status) -> None:  # noqa: ARG002
        # IMPORTANTE: este callback corre en el hilo realtime de PortAudio,
        # registrado por sounddevice con error=paAbort — cualquier excepción
        # sin capturar que escape de aquí aborta el OutputStream ENTERO
        # (no solo el chunk en curso), dejando el resto de la respuesta en
        # silencio permanente sin ningún error visible para el usuario. Por
        # eso nunca debe poder lanzar, pase lo que pase en la cola.
        try:
            self._out_callback_unsafe(outdata, frames, status)
        except Exception:
            log.exception("SounddeviceBackend: error en _out_callback, escribiendo silencio")
            outdata[:, 0] = 0

    def _out_callback_unsafe(self, outdata, frames, status) -> None:
        if status:
            log.warning("SounddeviceBackend playback status: %s", status)
        written = 0
        while written < frames:
            if self._play_leftover is not None:
                chunk = self._play_leftover
                self._play_leftover = None
            else:
                try:
                    chunk = self._play_q.get_nowait()
                except queue_mod.Empty:
                    break
            arr = np.frombuffer(chunk, dtype=np.int16)
            n = min(len(arr), frames - written)
            outdata[written:written + n, 0] = arr[:n]
            written += n
            if n < len(arr):
                self._play_leftover = arr[n:].tobytes()
        if written < frames:
            outdata[written:, 0] = 0

    async def play_notification(self) -> None:
        wave = synth_notification_tone(_TTS_SAMPLE_RATE)
        await self._enqueue_and_wait(wave)

    async def play_chunk(self, audio: bytes) -> None:
        if not audio:
            return
        await self._enqueue_and_wait(audio)

    async def _enqueue_and_wait(self, audio: bytes) -> None:
        # El chunking de red no garantiza alinear cada mensaje TTS a un
        # número par de bytes (tamaño de una muestra int16) — un chunk de
        # longitud impar rompería np.frombuffer en _out_callback. En vez de
        # dejarlo entrar en _play_q, arrastramos el byte suelto y lo
        # anteponemos al siguiente chunk para no perder muestras.
        audio = self._enqueue_carry + audio
        if len(audio) % 2 != 0:
            self._enqueue_carry = audio[-1:]
            audio = audio[:-1]
        else:
            self._enqueue_carry = b""
        if not audio:
            return
        duration = len(audio) / (_TTS_SAMPLE_RATE * 2)
        # El audio debe estar encolado ANTES de arrancar el stream: si
        # _ensure_output_stream() (que llama a stream.start()) corre primero,
        # el callback de PortAudio puede pedir datos con _play_q aún vacía
        # justo al arrancar, reproduciendo silencio y perdiendo el principio
        # del audio (click al inicio de cada beep/respuesta).
        self._play_q.put(audio)
        self._ensure_output_stream()
        # Dormir solo la duración real del chunk: _play_q ya desacopla
        # productor/consumidor, así que no hace falta (ni conviene) un
        # margen extra aquí — dormir de más retrasa la entrega del
        # siguiente chunk más de lo que el hardware tarda en consumir el
        # actual, vaciando la cola y produciendo huecos de silencio real
        # entre chunk y chunk (no distorsión: samples que no se juntan).
        await asyncio.sleep(duration)

    async def drain(self) -> None:
        if self._output_stream is not None:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._output_stream.stop)
            except Exception as exc:
                log.warning("SounddeviceBackend.drain stop: %s", exc)
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._output_stream.close)
            except Exception as exc:
                log.warning("SounddeviceBackend.drain close: %s", exc)
            self._output_stream = None
        # Vaciar play queue
        self._play_leftover = None
        self._enqueue_carry = b""
        while not self._play_q.empty():
            try:
                self._play_q.get_nowait()
            except queue_mod.Empty:
                break

    def reset(self) -> None:
        with self._lock:
            self._preroll.clear()
        self._play_leftover = None
        self._enqueue_carry = b""
        while not self._play_q.empty():
            try:
                self._play_q.get_nowait()
            except queue_mod.Empty:
                break
