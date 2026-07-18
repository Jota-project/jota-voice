"""Tests del servidor HTTP de control."""
from __future__ import annotations

import asyncio
import os
import stat
import tempfile
from pathlib import Path

from config import ControlConfig

HEADER = "X-Jota-Control-Token"


async def _send(port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    response = await asyncio.wait_for(reader.read(1024), timeout=2.0)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return response


def _cancel_request(token: str | None) -> bytes:
    lines = [b"POST /cancel HTTP/1.1\r\n", b"Host: localhost\r\n"]
    if token is not None:
        lines.append(f"{HEADER}: {token}\r\n".encode())
    lines.append(b"Content-Length: 0\r\n")
    lines.append(b"\r\n")
    return b"".join(lines)


def _unknown_request(token: str | None) -> bytes:
    lines = [b"GET /unknown HTTP/1.1\r\n", b"Host: localhost\r\n"]
    if token is not None:
        lines.append(f"{HEADER}: {token}\r\n".encode())
    lines.append(b"\r\n")
    return b"".join(lines)


async def _test_post_cancel_con_token_correcto_activa_evento() -> None:
    from app import control_server

    with tempfile.TemporaryDirectory() as tmp:
        token_path = os.path.join(tmp, "token")
        cancel_event = asyncio.Event()
        cfg = ControlConfig(port=18765, token_path=token_path)

        server_task = asyncio.create_task(control_server.run(cfg, cancel_event))
        await asyncio.sleep(0.05)
        token = Path(token_path).read_text().strip()

        response = await _send(18765, _cancel_request(token))

        assert b"200" in response, f"Esperaba 200, got: {response[:100]!r}"
        assert cancel_event.is_set(), "cancel_event debería estar activado"

        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


async def _test_post_cancel_sin_header_rechaza_401() -> None:
    from app import control_server

    with tempfile.TemporaryDirectory() as tmp:
        token_path = os.path.join(tmp, "token")
        cancel_event = asyncio.Event()
        cfg = ControlConfig(port=18771, token_path=token_path)

        server_task = asyncio.create_task(control_server.run(cfg, cancel_event))
        await asyncio.sleep(0.05)

        response = await _send(18771, _cancel_request(None))

        assert b"401" in response, f"Esperaba 401, got: {response[:100]!r}"
        assert not cancel_event.is_set(), "cancel_event NO debería activarse sin header"

        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


async def _test_post_cancel_con_token_incorrecto_rechaza_401() -> None:
    from app import control_server

    with tempfile.TemporaryDirectory() as tmp:
        token_path = os.path.join(tmp, "token")
        cancel_event = asyncio.Event()
        cfg = ControlConfig(port=18772, token_path=token_path)

        server_task = asyncio.create_task(control_server.run(cfg, cancel_event))
        await asyncio.sleep(0.05)
        # Aseguramos que el token real ya existe, pero mandamos otro distinto
        Path(token_path).read_text()

        response = await _send(18772, _cancel_request("token-equivocado"))

        assert b"401" in response, f"Esperaba 401, got: {response[:100]!r}"
        assert not cancel_event.is_set(), "cancel_event NO debería activarse con token incorrecto"

        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


async def _test_endpoint_desconocido_con_token_correcto_retorna_404() -> None:
    from app import control_server

    with tempfile.TemporaryDirectory() as tmp:
        token_path = os.path.join(tmp, "token")
        cancel_event = asyncio.Event()
        cfg = ControlConfig(port=18766, token_path=token_path)

        server_task = asyncio.create_task(control_server.run(cfg, cancel_event))
        await asyncio.sleep(0.05)
        token = Path(token_path).read_text().strip()

        response = await _send(18766, _unknown_request(token))

        assert b"404" in response, f"Esperaba 404, got: {response[:100]!r}"
        assert not cancel_event.is_set(), "cancel_event NO debería activarse"

        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


async def _test_puerto_ocupado_no_crashea() -> None:
    from app import control_server

    with tempfile.TemporaryDirectory() as tmp:
        token_path = os.path.join(tmp, "token")
        # Ocupar el puerto manualmente
        blocker = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 18767)
        cancel_event = asyncio.Event()
        cfg = ControlConfig(port=18767, token_path=token_path)

        # Debe retornar sin excepción
        await asyncio.wait_for(control_server.run(cfg, cancel_event), timeout=2.0)

        blocker.close()
        await blocker.wait_closed()


async def _test_rate_limit_devuelve_429_tras_exceder_limite() -> None:
    from app import control_server

    with tempfile.TemporaryDirectory() as tmp:
        token_path = os.path.join(tmp, "token")
        cancel_event = asyncio.Event()
        cfg = ControlConfig(
            port=18768,
            token_path=token_path,
            rate_limit_max_requests=2,
            rate_limit_window_s=10.0,
        )

        server_task = asyncio.create_task(control_server.run(cfg, cancel_event))
        await asyncio.sleep(0.05)
        token = Path(token_path).read_text().strip()

        responses = [await _send(18768, _cancel_request(token)) for _ in range(3)]

        assert b"200" in responses[0], f"1ª petición esperaba 200, got: {responses[0][:100]!r}"
        assert b"200" in responses[1], f"2ª petición esperaba 200, got: {responses[1][:100]!r}"
        assert b"429" in responses[2], f"3ª petición esperaba 429, got: {responses[2][:100]!r}"

        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


async def _test_token_se_autogenera_con_permisos_600() -> None:
    from app import control_server

    with tempfile.TemporaryDirectory() as tmp:
        token_path = os.path.join(tmp, "control_token")
        cancel_event = asyncio.Event()
        cfg = ControlConfig(port=18769, token_path=token_path)

        server_task = asyncio.create_task(control_server.run(cfg, cancel_event))
        await asyncio.sleep(0.05)

        assert os.path.isfile(token_path), "El fichero de token debería haberse autogenerado"
        mode = stat.S_IMODE(os.stat(token_path).st_mode)
        assert mode == 0o600, f"Esperaba permisos 600, got {oct(mode)}"
        token = Path(token_path).read_text().strip()
        assert len(token) >= 32, "El token autogenerado parece demasiado corto"

        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


async def _test_token_existente_se_reutiliza_tal_cual() -> None:
    from app import control_server

    with tempfile.TemporaryDirectory() as tmp:
        token_path = os.path.join(tmp, "control_token")
        Path(token_path).write_text("token-fijo-de-prueba")
        os.chmod(token_path, 0o600)
        cancel_event = asyncio.Event()
        cfg = ControlConfig(port=18770, token_path=token_path)

        server_task = asyncio.create_task(control_server.run(cfg, cancel_event))
        await asyncio.sleep(0.05)

        response = await _send(18770, _cancel_request("token-fijo-de-prueba"))

        assert b"200" in response, f"Esperaba 200, got: {response[:100]!r}"
        assert cancel_event.is_set()
        assert Path(token_path).read_text() == "token-fijo-de-prueba", (
            "No debería regenerarse un token que ya existía"
        )

        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


def test_post_cancel_con_token_correcto_activa_evento() -> None:
    asyncio.run(_test_post_cancel_con_token_correcto_activa_evento())


def test_post_cancel_sin_header_rechaza_401() -> None:
    asyncio.run(_test_post_cancel_sin_header_rechaza_401())


def test_post_cancel_con_token_incorrecto_rechaza_401() -> None:
    asyncio.run(_test_post_cancel_con_token_incorrecto_rechaza_401())


def test_endpoint_desconocido_con_token_correcto_retorna_404() -> None:
    asyncio.run(_test_endpoint_desconocido_con_token_correcto_retorna_404())


def test_puerto_ocupado_no_crashea() -> None:
    asyncio.run(_test_puerto_ocupado_no_crashea())


def test_rate_limit_devuelve_429_tras_exceder_limite() -> None:
    asyncio.run(_test_rate_limit_devuelve_429_tras_exceder_limite())


def test_token_se_autogenera_con_permisos_600() -> None:
    asyncio.run(_test_token_se_autogenera_con_permisos_600())


def test_token_existente_se_reutiliza_tal_cual() -> None:
    asyncio.run(_test_token_existente_se_reutiliza_tal_cual())
