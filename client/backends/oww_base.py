"""Interfaz OwWBackend — wake word detection agnóstica de plataforma."""
from __future__ import annotations

from typing import Callable, Coroutine, Protocol, runtime_checkable


@runtime_checkable
class OwWBackend(Protocol):
    """Backend de wake word detection.

    Implementado por WyomingBackend. Diseñado para correr como task background
    persistente; publica wake_word_detected vía el callback on_wake_word.
    """

    async def connect_with_backoff(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def send_audio(self, pcm_int16: bytes) -> None: ...
    async def wait_for_detection(self) -> str: ...
    async def run_forever(
        self,
        audio: "AudioBackend",
        on_wake_word: Callable[[str], Coroutine],
    ) -> None: ...