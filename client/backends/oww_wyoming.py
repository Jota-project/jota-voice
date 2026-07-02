"""Stub temporal — implementación real viene en Task 8."""
from config import OWWConfig


class WyomingBackend:
    def __init__(self, cfg: OWWConfig, on_wake_word) -> None:
        self._cfg = cfg
        self._on_wake_word = on_wake_word

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def connect_with_backoff(self) -> None:
        pass

    async def send_audio(self, frame: bytes) -> None:
        pass

    async def wait_for_detection(self) -> str:
        return ""
