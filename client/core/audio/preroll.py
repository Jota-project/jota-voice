"""Ring-buffer de pre-roll de audio (últimos N segundos antes de la wake word)."""

import collections


class PrerollBuffer:
    """Acumula los últimos N segundos de frames float32 en un ring-buffer."""

    def __init__(self, seconds: float, sample_rate: int, frames_per_buffer: int) -> None:
        maxlen = int(seconds * sample_rate / frames_per_buffer)
        self._buffer: collections.deque[bytes] = collections.deque(maxlen=maxlen)

    @property
    def maxlen(self) -> int:
        return self._buffer.maxlen

    def append(self, frame: bytes) -> None:
        self._buffer.append(frame)

    def clear(self) -> None:
        self._buffer.clear()

    def get(self) -> bytes:
        """Devuelve los frames acumulados como bytes float32 concatenados."""
        return b"".join(self._buffer)
