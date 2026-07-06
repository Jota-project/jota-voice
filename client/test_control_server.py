"""Tests del servidor HTTP de control."""
from __future__ import annotations

import asyncio
import os
import sys

_here = os.path.dirname(__file__)
if _here not in sys.path:
    sys.path.insert(0, _here)

from config import ControlConfig


async def _test_post_cancel_activa_evento() -> None:
    from app import control_server

    cancel_event = asyncio.Event()
    cfg = ControlConfig(port=18765)

    server_task = asyncio.create_task(control_server.run(cfg, cancel_event))
    await asyncio.sleep(0.05)

    reader, writer = await asyncio.open_connection("127.0.0.1", 18765)
    writer.write(
        b"POST /cancel HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Length: 0\r\n"
        b"\r\n"
    )
    await writer.drain()
    response = await asyncio.wait_for(reader.read(1024), timeout=2.0)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass

    assert b"200" in response, f"Esperaba 200, got: {response[:100]!r}"
    assert cancel_event.is_set(), "cancel_event debería estar activado"

    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass


async def _test_endpoint_desconocido_retorna_404() -> None:
    from app import control_server

    cancel_event = asyncio.Event()
    cfg = ControlConfig(port=18766)

    server_task = asyncio.create_task(control_server.run(cfg, cancel_event))
    await asyncio.sleep(0.05)

    reader, writer = await asyncio.open_connection("127.0.0.1", 18766)
    writer.write(b"GET /unknown HTTP/1.1\r\nHost: localhost\r\n\r\n")
    await writer.drain()
    response = await asyncio.wait_for(reader.read(1024), timeout=2.0)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass

    assert b"404" in response, f"Esperaba 404, got: {response[:100]!r}"
    assert not cancel_event.is_set(), "cancel_event NO debería activarse"

    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass


async def _test_puerto_ocupado_no_crashea() -> None:
    from app import control_server

    # Ocupar el puerto manualmente
    blocker = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 18767)
    cancel_event = asyncio.Event()
    cfg = ControlConfig(port=18767)

    # Debe retornar sin excepción
    await asyncio.wait_for(control_server.run(cfg, cancel_event), timeout=2.0)

    blocker.close()
    await blocker.wait_closed()


def test_post_cancel_activa_evento() -> None:
    asyncio.run(_test_post_cancel_activa_evento())


def test_endpoint_desconocido_retorna_404() -> None:
    asyncio.run(_test_endpoint_desconocido_retorna_404())


def test_puerto_ocupado_no_crashea() -> None:
    asyncio.run(_test_puerto_ocupado_no_crashea())


if __name__ == "__main__":
    asyncio.run(_test_post_cancel_activa_evento())
    asyncio.run(_test_endpoint_desconocido_retorna_404())
    asyncio.run(_test_puerto_ocupado_no_crashea())
    print("=== TODOS LOS TESTS PASARON ===")
