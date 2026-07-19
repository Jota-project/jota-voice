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


async def _test_post_cancel_con_header_no_ascii_rechaza_401() -> None:
    """Un valor de header no-ASCII (bytes corruptos/binarios) no debe tumbar
    la conexión sin respuesta — debe rechazarse limpiamente con 401, igual
    que cualquier otro token inválido."""
    from app import control_server

    with tempfile.TemporaryDirectory() as tmp:
        token_path = os.path.join(tmp, "token")
        cancel_event = asyncio.Event()
        cfg = ControlConfig(port=18773, token_path=token_path)

        server_task = asyncio.create_task(control_server.run(cfg, cancel_event))
        await asyncio.sleep(0.05)

        request = (
            b"POST /cancel HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Jota-Control-Token: \xff\xff\xff\r\n"
            b"Content-Length: 0\r\n"
            b"\r\n"
        )
        response = await _send(18773, request)

        assert b"401" in response, f"Esperaba 401, got: {response[:100]!r}"
        assert not cancel_event.is_set()

        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


async def _test_load_or_create_token_no_pisa_si_aparece_justo_al_crear(monkeypatch) -> None:
    """Si is_file() dice que no existe (carrera con otro proceso arrancando
    a la vez) pero el fichero aparece justo antes del open() con O_EXCL, no
    debe perderse el token ya escrito: debe leerse el existente, no
    sobreescribirlo con uno nuevo."""
    from app import control_server

    with tempfile.TemporaryDirectory() as tmp:
        token_path = Path(tmp) / "token"
        token_path.write_text("token-de-otro-proceso")
        os.chmod(token_path, 0o600)

        monkeypatch.setattr(Path, "is_file", lambda self: False)

        token = control_server._load_or_create_token(token_path)

        assert token == "token-de-otro-proceso", (
            "Debería leer el token ya escrito por el otro proceso, no generar uno nuevo"
        )


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


def test_post_cancel_con_header_no_ascii_rechaza_401() -> None:
    asyncio.run(_test_post_cancel_con_header_no_ascii_rechaza_401())


def test_load_or_create_token_no_pisa_si_aparece_justo_al_crear(monkeypatch) -> None:
    asyncio.run(_test_load_or_create_token_no_pisa_si_aparece_justo_al_crear(monkeypatch))


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


async def _test_load_or_create_token_regenera_si_fichero_esta_vacio() -> None:
    """Si el fichero de token existe pero está vacío (corrupción, truncado,
    edición manual accidental), no se devuelve la cadena vacía — eso
    convertiría compare_digest(b'', b'') en True y abriría un bypass de
    auth (cualquier petición sin header se aceptaría como token válido)."""
    from app import control_server

    with tempfile.TemporaryDirectory() as tmp:
        token_path = Path(tmp) / "token"
        token_path.write_text("")  # fichero existente pero vacío
        os.chmod(token_path, 0o600)

        token = control_server._load_or_create_token(token_path)

        assert token != "", "Un fichero vacío debe regenerar el token, no devolver ''"
        assert len(token) == 64, f"secrets.token_hex(32) produce 64 chars; got {len(token)}"
        on_disk = token_path.read_text().strip()
        assert on_disk == token, "El fichero en disco debe contener el token regenerado"


async def _test_load_or_create_token_regenera_si_fichero_solo_whitespace() -> None:
    """Variante del anterior: contenido con solo whitespace también cuenta
    como 'vacío' a efectos de auth (compare_digest sobre strip vacío es
    True). Mismo tratamiento."""
    from app import control_server

    with tempfile.TemporaryDirectory() as tmp:
        token_path = Path(tmp) / "token"
        token_path.write_text("   \n\n\t  \n")
        os.chmod(token_path, 0o600)

        token = control_server._load_or_create_token(token_path)

        assert token, "Fichero solo-whitespace debe regenerar el token"
        assert len(token) == 64


async def _test_control_server_con_token_vacio_rechaza_peticiones() -> None:
    """End-to-end: con el fichero de token vacío preexistente, el servidor
    NO debe dejar pasar una petición que no envíe el token. Sin el fix,
    el servidor leería '' del fichero y compare_digest(b'', b'') sería
    True → 200 en vez de 401."""
    from app import control_server

    with tempfile.TemporaryDirectory() as tmp:
        token_path = Path(tmp) / "token"
        token_path.write_text("")  # bypass latente
        os.chmod(token_path, 0o600)
        cancel_event = asyncio.Event()
        cfg = ControlConfig(port=18774, token_path=token_path)

        server_task = asyncio.create_task(control_server.run(cfg, cancel_event))
        await asyncio.sleep(0.05)

        # Petición sin token: debe ser 401, no 200
        response = await _send(18774, _cancel_request(None))
        assert b"401" in response, (
            f"Bypass de auth: con token vacío, petición sin token devuelve "
            f"{response[:100]!r} en vez de 401"
        )
        assert not cancel_event.is_set(), (
            "cancel_event NO debería activarse si hay bypass de auth"
        )

        # Petición con token vacío explícito: también 401
        response_empty = await _send(18774, _cancel_request(""))
        assert b"401" in response_empty, (
            f"Bypass de auth: token vacío explícito devuelve "
            f"{response_empty[:100]!r} en vez de 401"
        )

        # El token nuevo en disco SÍ funciona (control server lo regeneró)
        new_token = token_path.read_text().strip()
        assert new_token, "El servidor debió regenerar el token"
        response_ok = await _send(18774, _cancel_request(new_token))
        assert b"200" in response_ok, (
            f"Con el token regenerado, la petición con ese token debe pasar: "
            f"{response_ok[:100]!r}"
        )

        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


def test_load_or_create_token_regenera_si_fichero_esta_vacio() -> None:
    asyncio.run(_test_load_or_create_token_regenera_si_fichero_esta_vacio())


def test_load_or_create_token_regenera_si_fichero_solo_whitespace() -> None:
    asyncio.run(_test_load_or_create_token_regenera_si_fichero_solo_whitespace())


def test_control_server_con_token_vacio_rechaza_peticiones() -> None:
    asyncio.run(_test_control_server_con_token_vacio_rechaza_peticiones())
