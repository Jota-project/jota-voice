"""SounddeviceBackend — captura y reproducción con sounddevice (PortAudio).

Plataformas: macOS (CoreAudio), Linux (ALSA/PulseAudio). No funciona en Termux
(Android) — para eso usar TermuxBackend.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import queue as queue_mod
import threading

import numpy as np

from config import AudioConfig

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
        self._input_stream = None
        self._output_stream = None
        self._play_q: queue_mod.Queue[bytes] = queue_mod.Queue()
        self._capture_thread: threading.Thread | None = None
        self._capture_stop = threading.Event()

        preroll_frames = int(
            cfg.preroll_seconds * cfg.sample_rate / cfg.frames_per_buffer
        )
        self._preroll: collections.deque[bytes] = collections.deque(maxlen=preroll_frames)
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
        self._capture_stop.clear()

        def _callback(indata, frames, time, status):  # noqa: ARG001
            if status:
                log.warning("SounddeviceBackend capture status: %s", status)
            chunk = bytes(indata)  # float32 contiguo
            with self._lock:
                self._preroll.append(chunk)
            if self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._capture_q.put_nowait, chunk)

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
        while not self._play_q.empty():
            try:
                self._play_q.get_nowait()
            except queue_mod.Empty:
                break

    # --- acceso a datos ---

    def get_queue(self) -> asyncio.Queue[bytes]:
        if self._capture_q is None:
            raise RuntimeError("SounddeviceBackend.get_queue() antes de start()")
        return self._capture_q

    def get_preroll(self) -> bytes:
        with self._lock:
            return b"".join(self._preroll)

    def is_silence(self, frame: bytes) -> bool:
        samples = np.frombuffer(frame, dtype=np.float32)
        if samples.size == 0:
            return True
        rms = float(np.sqrt(np.mean(samples ** 2))) * 32768.0
        return rms < self._cfg.vad_rms_threshold

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

        def _out_callback(outdata, frames, time, status):  # noqa: ARG001
            try:
                data = self._play_q.get_nowait()
            except queue_mod.Empty:
                outdata[:] = b"\x00\x00" * frames
                return
            arr = np.frombuffer(data, dtype=np.int16)
            n = min(len(arr), frames)
            outdata[:n, 0] = arr[:n]
            if n < frames:
                outdata[n:, 0] = 0

        self._output_stream = sd.OutputStream(**out_kwargs, callback=_out_callback)
        self._output_stream.start()

    async def play_notification(self) -> None:
        rate = _TTS_SAMPLE_RATE
        segments = []
        for freq, dur in [(587.0, 0.10), (880.0, 0.18)]:
            n = int(rate * dur)
            t = np.linspace(0, dur, n, endpoint=False)
            envelope = np.exp(-t * 18.0)
            tone = np.sin(2 * np.pi * freq * t) * 0.6 + np.sin(2 * np.pi * freq * 2 * t) * 0.2
            segments.append((tone * envelope * 32767 * 0.9).astype(np.int16))
            segments.append(np.zeros(int(rate * 0.03), dtype=np.int16))
        wave = np.concatenate(segments).tobytes()
        await self._enqueue_and_wait(wave)

    async def play_chunk(self, audio: bytes) -> None:
        if not audio:
            return
        await self._enqueue_and_wait(audio)

    async def _enqueue_and_wait(self, audio: bytes) -> None:
        self._ensure_output_stream()
        duration = len(audio) / (_TTS_SAMPLE_RATE * 2)
        self._play_q.put(audio)
        await asyncio.sleep(duration + 0.05)

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
        while not self._play_q.empty():
            try:
                self._play_q.get_nowait()
            except queue_mod.Empty:
                break

    def reset(self) -> None:
        with self._lock:
            self._preroll.clear()
        while not self._play_q.empty():
            try:
                self._play_q.get_nowait()
            except queue_mod.Empty:
                break