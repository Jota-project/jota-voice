"""NullDisplayBackend — backend no-op para dispositivos sin display."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class NullDisplayBackend:
    """Display no-op. Loguea a DEBUG; nunca falla."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def send(self, type: str, data: dict[str, Any]) -> None:
        log.debug("NullDisplayBackend: %s %s", type, data)
