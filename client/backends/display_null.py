"""Stub temporal — implementación real viene en Task 4."""


class NullDisplayBackend:
    def __init__(self) -> None:
        pass

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
