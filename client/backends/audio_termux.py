"""Stub temporal — implementación real viene en Task 8."""
from config import AudioConfig


class TermuxBackend:
    def __init__(self, cfg: AudioConfig) -> None:
        self._cfg = cfg

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def get_queue(self):
        pass

    def get_preroll(self) -> bytes:
        return b""

    def is_silence(self, frame) -> bool:
        return True

    async def play_notification(self) -> None:
        pass

    async def play_chunk(self, audio: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass

    def reset(self) -> None:
        pass
