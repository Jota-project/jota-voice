"""NullMenubarBackend — backend no-op para Linux, Termux, Windows y tests."""
from __future__ import annotations

import logging

from .menubar_base import MenubarCommands

log = logging.getLogger(__name__)


class NullMenubarBackend:
    """Implementación no-op. Loguea a DEBUG; nunca falla."""

    def set_state(self, state: str) -> None:
        log.debug("NullMenubarBackend: state=%s", state)

    def set_status_text(self, text: str) -> None:
        log.debug("NullMenubarBackend: text=%r", text)

    def set_listening_paused(self, paused: bool) -> None:
        log.debug("NullMenubarBackend: paused=%s", paused)

    def set_errors_count(self, n: int) -> None:
        log.debug("NullMenubarBackend: errors=%d", n)

    def set_commands(self, cmds: MenubarCommands) -> None:
        log.debug("NullMenubarBackend: commands registered")
