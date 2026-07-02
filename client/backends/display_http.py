"""Stub temporal — implementación real viene en Task 4."""
from config import DisplayConfig


class HttpDisplayBackend:
    def __init__(self, cfg: DisplayConfig) -> None:
        self._cfg = cfg

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def set_state(self, state: str) -> None:
        pass

    async def set_text(self, text: str) -> None:
        pass

    async def show_avatar(self) -> None:
        pass
