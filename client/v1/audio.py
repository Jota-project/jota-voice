import asyncio
import collections
import threading
from typing import Optional

import numpy as np
import pyaudio

from config import AudioConfig


class AudioIO:
    def __init__(self, cfg: AudioConfig) -> None:
        self._cfg = cfg
        self._pa: Optional[pyaudio.PyAudio] = None
        self._capture_stream: Optional[pyaudio.Stream] = None
        self._playback_stream: Optional[pyaudio.Stream] = None
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

        preroll_frames = int(cfg.preroll_seconds * cfg.sample_rate / cfg.frames_per_buffer)
        self._preroll: collections.deque[bytes] = collections.deque(maxlen=preroll_frames)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._pa = pyaudio.PyAudio()
        self._capture_stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=self._cfg.channels,
            rate=self._cfg.sample_rate,
            input=True,
            frames_per_buffer=self._cfg.frames_per_buffer,
            stream_callback=self._capture_callback,
        )
        self._capture_stream.start_stream()

    async def stop(self) -> None:
        if self._capture_stream:
            self._capture_stream.stop_stream()
            self._capture_stream.close()
        if self._playback_stream:
            self._playback_stream.stop_stream()
            self._playback_stream.close()
        if self._pa:
            self._pa.terminate()

    def _capture_callback(
        self, in_data: bytes, frame_count: int, time_info: dict, status: int
    ) -> tuple:
        float32_bytes = _int16_to_float32(in_data)
        self._preroll.append(float32_bytes)
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._queue.put_nowait, float32_bytes)
        return (None, pyaudio.paContinue)

    def get_capture_queue(self) -> asyncio.Queue[bytes]:
        return self._queue

    def get_preroll(self) -> bytes:
        return b"".join(self._preroll)

    def is_silence(self, frame_bytes: bytes) -> bool:
        frame_int16 = np.frombuffer(frame_bytes, dtype=np.float32) * 32768.0
        rms = float(np.sqrt(np.mean(frame_int16 ** 2)))
        return rms < self._cfg.vad_rms_threshold

    async def play_chunk(self, audio_bytes: bytes) -> None:
        if self._playback_stream is None:
            self._playback_stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=24000,
                output=True,
            )
        await asyncio.get_running_loop().run_in_executor(
            None, self._playback_stream.write, audio_bytes
        )

    async def drain_playback(self) -> None:
        if self._playback_stream:
            self._playback_stream.stop_stream()
            self._playback_stream.close()
            self._playback_stream = None


def _int16_to_float32(data: bytes) -> bytes:
    arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    return arr.tobytes()
