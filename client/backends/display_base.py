"""Interfaz DisplayBackend — envío de estado a una UI externa (opcional).

El vocabulario de mensajes es el del protocolo display
(docs/display-protocol.md): ``DisplayClient`` traduce los VoiceEvent del bus
a mensajes ``(type, data)`` de ese protocolo y cada backend los entrega a su
manera.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# Tipos de mensaje servidor -> display (docs/display-protocol.md §5).
MESSAGE_TYPES = frozenset({
    "state",
    "turn_start",
    "text_update",
    "tool_call",
    "error",
    "cancelled",
    "health",
})

# Valores de `state.value`.
STATES = frozenset({"idle", "listening", "thinking", "response"})


@runtime_checkable
class DisplayBackend(Protocol):
    """Backend de display.

    Implementaciones:
      - WsDisplayBackend: servidor WebSocket local (protocolo completo).
      - HttpDisplayBackend: POST /state al kiosk de jota-display
        (compatibilidad; solo entiende `state` y `text_update`).
      - NullDisplayBackend: no-op.

    ``send`` es SÍNCRONO y no bloquea nunca (encola): un display lento o
    caído no puede frenar a DisplayClient ni, por tanto, acumular backlog en
    el EventBus (issue #64). Cada backend acota su propia cola.
    """

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def send(self, type: str, data: dict[str, Any]) -> None: ...
