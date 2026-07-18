"""Contratos del UI layer: Protocol, comandos y estado compartido."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class MenubarBackend(Protocol):
    """Contrato que cualquier backend de UI debe cumplir."""

    def set_state(self, state: str) -> None: ...
    def set_status_text(self, text: str) -> None: ...
    def set_listening_paused(self, paused: bool) -> None: ...
    def set_errors_count(self, n: int) -> None: ...
    def set_commands(self, cmds: "MenubarCommands") -> None: ...


@dataclass
class MenubarCommands:
    """Callbacks que la UI invoca hacia asyncio. La UI no escribe al bus."""

    on_toggle_pause: Callable[[], None]
    on_open_logs: Callable[[], None]
    on_open_config: Callable[[], None]
    on_shutdown_service: Callable[[], None]
    on_quit: Callable[[], None]


@dataclass
class _SharedState:
    """Estado proyectado del EventBus para el hilo de Cocoa.

    El hilo asyncio (MenubarClient) lo escribe; el hilo Cocoa (NSTimer) lo lee.
    El lock evita lecturas a medias en campos de más de 4 bytes (Python ints
    en CPython son atómicos, pero el snapshot consistente de los 4 campos sí
    necesita lock).
    """

    state: str = "idle"
    last_text: str = ""
    errors_count: int = 0
    listening_paused: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def read(self) -> tuple[str, str, int, bool]:
        with self._lock:
            return (self.state, self.last_text, self.errors_count, self.listening_paused)

    def update(
        self,
        state: str | None = None,
        last_text: str | None = None,
        errors_count: int | None = None,
        listening_paused: bool | None = None,
    ) -> None:
        with self._lock:
            if state is not None:
                self.state = state
            if last_text is not None:
                self.last_text = last_text
            if errors_count is not None:
                self.errors_count = errors_count
            if listening_paused is not None:
                self.listening_paused = listening_paused
