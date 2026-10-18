"""Fakes del WebSocket del gateway para tests de GatewayClient (issue #112).

Antes vivían duplicados dentro de test_gateway_client.py (dos ``_FakeWS``
con implementaciones distintas, la segunda pisando a la primera, issue
#37). Cualquier test que necesite un socket falso debería salir de aquí.

- :class:`FakeWS` / :class:`FakeWSRaiseAfter`: iterables async para probar
  ``receive()`` (sin ``close_code``: se comportan como un socket abierto).
- :func:`make_ws`: mock con autospec de la ``ClientConnection`` real de
  websockets, para probar ``connect()``/``send_*``. Un método que la librería
  no tenga hace fallar el test en vez de aceptarse en silencio (issue #38).
"""
from __future__ import annotations

import asyncio
from typing import Any, Iterable
from unittest.mock import MagicMock, create_autospec

from websockets.asyncio.client import ClientConnection


class FakeWS:
    """Async-iterable mínimo que entrega ``messages`` y se agota."""

    def __init__(self, messages: Iterable[Any]) -> None:
        self._messages = list(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


class FakeWSRaiseAfter:
    """Async-iterable que entrega ``messages`` y luego lanza ``exc``.

    Sirve para probar que receive() distingue ConnectionClosedOK de
    ConnectionClosedError."""

    def __init__(self, messages: Iterable[Any], exc: BaseException) -> None:
        self._messages = list(messages)
        self._exc = exc

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._messages:
            return self._messages.pop(0)
        raise self._exc


def make_ws(recv: Iterable[Any] | None = None) -> MagicMock:
    """``ClientConnection`` falsa (autospec) en estado abierto.

    ``send``/``recv``/``close`` son AsyncMock con la firma real. Si se pasa
    ``recv``, cada ``await ws.recv()`` devuelve (o lanza, si es excepción)
    el siguiente elemento. Para un recv que no vuelve nunca:
    ``ws.recv.side_effect = never``.
    """
    ws = create_autospec(ClientConnection, instance=True)
    # Atributos de instancia que autospec no ve (los pone __init__): una
    # conexión abierta tiene close_code None.
    ws.close_code = None
    ws.close_reason = None
    ws.transport = create_autospec(asyncio.Transport, instance=True)
    if recv is not None:
        ws.recv.side_effect = list(recv)
    return ws


async def never(*_args: Any, **_kwargs: Any) -> Any:
    """Corrutina que no termina nunca (hasta que la cancelen)."""
    await asyncio.Event().wait()
