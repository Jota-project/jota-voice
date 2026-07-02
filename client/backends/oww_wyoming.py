"""WyomingBackend — wake word via Wyoming TCP (default port 10401)."""
from __future__ import annotations

from typing import Awaitable, Callable

from config import OWWConfig
from oww_client import OWWClient


class WyomingBackend:
    """Backend de wake word usando el protocolo Wyoming (JSON-lines over TCP).

    Funciona con cualquier wyoming-openwakeword (Docker en Mac, venv en Termux).
    """

    def __init__(self, cfg: OWWConfig, on_wake_word: Callable[[str], Awaitable[None]]) -> None:
        self._cfg = cfg
        self._on_wake_word = on_wake_word
        self._client = OWWClient(cfg)

    async def connect_with_backoff(self) -> None:
        await self._client.connect_with_backoff()

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def send_audio(self, pcm_int16: bytes) -> None:
        await self._client.send_audio(pcm_int16)

    async def wait_for_detection(self) -> str:
        return await self._client.wait_for_detection()

    async def run_forever(self, audio, on_wake_word: Callable[[str], Awaitable[None]]) -> None:
        await self._client.run_forever(audio, on_wake_word)