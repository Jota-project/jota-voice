"""NullDisplayBackend — backend no-op para dispositivos sin display (ej. Mac sin kiosk)."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class NullDisplayBackend:
    """Display no-op. Loguea a DEBUG; nunca falla."""

    async def update(self, state: str, **kwargs) -> None:
        log.debug("NullDisplayBackend: state=%s kwargs=%s", state, kwargs)