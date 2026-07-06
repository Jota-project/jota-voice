"""Tests de GatewayClient — no requieren servidor real."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

# --- Stubs ---
if "websockets" not in sys.modules:
    stub = types.ModuleType("websockets")
    exc_stub = types.ModuleType("websockets.exceptions")
    class ConnectionClosed(Exception): pass
    exc_stub.ConnectionClosed = ConnectionClosed
    stub.exceptions = exc_stub
    stub.connect = AsyncMock()
    sys.modules["websockets"] = stub
    sys.modules["websockets.exceptions"] = exc_stub

_here = os.path.dirname(__file__)
if _here not in sys.path:
    sys.path.insert(0, _here)

from config import GatewayConfig
from backends.gateway_client import GatewayClient


async def _test_send_cancel_envia_mensaje() -> None:
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    ws_mock = MagicMock()
    ws_mock.send = AsyncMock()
    client._ws = ws_mock

    await client.send_cancel()

    ws_mock.send.assert_awaited_once()
    sent = json.loads(ws_mock.send.call_args[0][0])
    assert sent == {"type": "cancel"}, f"Esperaba {{\"type\":\"cancel\"}}, got {sent}"


async def _test_send_cancel_sin_ws_lanza_error() -> None:
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)  # _ws = None
    try:
        await client.send_cancel()
        raise AssertionError("Debería haber lanzado RuntimeError")
    except RuntimeError as exc:
        assert "no conectado" in str(exc)


def test_send_cancel_envia_mensaje() -> None:
    asyncio.run(_test_send_cancel_envia_mensaje())


def test_send_cancel_sin_ws_lanza_error() -> None:
    asyncio.run(_test_send_cancel_sin_ws_lanza_error())


if __name__ == "__main__":
    asyncio.run(_test_send_cancel_envia_mensaje())
    asyncio.run(_test_send_cancel_sin_ws_lanza_error())
    print("=== TODOS LOS TESTS PASARON ===")
