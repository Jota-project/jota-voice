"""Interfaz DisplayBackend — envío de estado a una UI externa (opcional)."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DisplayBackend(Protocol):
    """Backend de display: traduce estado del asistente a una UI local/remota.

    Implementado por HttpDisplayBackend (POST a jota-display) y NullDisplayBackend (no-op).
    """

    async def update(self, state: str, **kwargs: Any) -> None: ...
