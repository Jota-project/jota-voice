"""Tests de WsDisplayBackend — servidor WebSocket local del protocolo display
(docs/display-protocol.md).

Portado de la rama feature/display-ws (agosto) sobre el main actual. Cubre lo
que antes probaba test_control_server.py (token 0600, regeneración, permisos,
rate limit, puerto ocupado) más lo específico de WS: `sync` al conectar
(idempotencia de reconexión), broadcast a varios displays, comando `cancel`
sobre la misma conexión, health con caducidad, y las issues #64, #106 y #115.

Puertos efímeros (port=0, como test_control_server tras #76): nada de
puertos fijos que choquen con otra suite corriendo en la misma máquina.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
from pathlib import Path

import pytest
import pytest_asyncio
import websockets

from backends import display_ws
from backends.display_ws import (
    PROTOCOL_VERSION,
    TOKEN_HEADER,
    WsDisplayBackend,
    _load_or_create_token,
)
from config import DisplayConfig

from tests.fakes.waiting import wait_until


def _cfg(tmp_path: Path, **overrides) -> DisplayConfig:
    kw = {"backend": "ws", "port": 0, "token_path": str(tmp_path / "token"), **overrides}
    return DisplayConfig(**kw)


async def _connect(backend: WsDisplayBackend, token: str | None):
    headers = {TOKEN_HEADER: token} if token is not None else {}
    return await websockets.connect(
        f"ws://127.0.0.1:{backend.port}", additional_headers=headers, open_timeout=2.0
    )


async def _recv(ws) -> dict:
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))


@pytest_asyncio.fixture
async def server(tmp_path: Path):
    """Backend arrancado + token; para tests que no tocan la config."""
    cancels: list[dict] = []
    backend = WsDisplayBackend(_cfg(tmp_path), on_cancel=lambda: cancels.append({}))
    await backend.start()
    token = (tmp_path / "token").read_text().strip()
    try:
        yield backend, token, cancels
    finally:
        await backend.stop()


# ---------------------------------------------------------------------------
# Token (fichero 0600, mismas garantías que tenía control_server + #115)
# ---------------------------------------------------------------------------


def test_init_no_toca_filesystem(tmp_path: Path) -> None:
    """Instanciar (p.ej. en un test que no arranca) no escribe en disco."""
    WsDisplayBackend(_cfg(tmp_path))
    assert not (tmp_path / "token").exists()


@pytest.mark.asyncio
async def test_token_se_autogenera_con_permisos_600(server, tmp_path: Path) -> None:
    _, token, _ = server
    mode = stat.S_IMODE(os.stat(tmp_path / "token").st_mode)
    assert mode == 0o600, oct(mode)
    assert len(token) == 64


def test_token_existente_se_reutiliza_tal_cual(tmp_path: Path) -> None:
    path = tmp_path / "token"
    path.write_text("token-fijo-de-prueba")
    os.chmod(path, 0o600)
    assert _load_or_create_token(path) == "token-fijo-de-prueba"
    assert path.read_text() == "token-fijo-de-prueba"


@pytest.mark.parametrize("contenido", ["", "   \n\n\t  \n"])
def test_token_vacio_o_whitespace_se_regenera(tmp_path: Path, contenido: str, monkeypatch) -> None:
    """Un fichero vacío no puede devolver '' (compare_digest(b'', b'') es
    True: bypass de auth, #108)."""
    path = tmp_path / "token"
    path.write_text(contenido)
    os.chmod(path, 0o600)
    monkeypatch.setattr(display_ws, "_EMPTY_TOKEN_WAIT_S", 0.01)
    token = _load_or_create_token(path)
    assert len(token) == 64
    assert path.read_text().strip() == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_token_con_permisos_laxos_se_corrige(tmp_path: Path, caplog) -> None:
    """Re-verificación de permisos al arrancar aunque el token sea válido (#105)."""
    import logging

    path = tmp_path / "token"
    path.write_text("token-con-permisos-laxos")
    os.chmod(path, 0o644)
    with caplog.at_level(logging.WARNING):
        assert _load_or_create_token(path) == "token-con-permisos-laxos"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert any("permisos laxos" in m for m in caplog.messages)


def test_token_regenerado_desde_fichero_laxo_queda_en_600(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "token"
    path.write_text("")
    os.chmod(path, 0o644)
    monkeypatch.setattr(display_ws, "_EMPTY_TOKEN_WAIT_S", 0.01)
    _load_or_create_token(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_token_nunca_es_visible_vacio_al_crearlo(tmp_path: Path, monkeypatch) -> None:
    """Issue #115: el fichero se publica con os.link desde un temporal ya
    escrito, así que en ningún instante existe vacío en la ruta final. Se
    comprueba mirando la ruta justo en el momento del link."""
    path = tmp_path / "token"
    seen: list[str] = []
    real_link = os.link

    def _spy_link(src, dst, *a, **kw):
        seen.append(Path(src).read_text())
        assert not Path(dst).exists()
        real_link(src, dst, *a, **kw)
        seen.append(Path(dst).read_text())

    monkeypatch.setattr(display_ws.os, "link", _spy_link)
    token = _load_or_create_token(path)
    assert seen == [token, token]
    assert [p.name for p in tmp_path.iterdir()] == ["token"]  # sin temporales


def test_token_otro_proceso_gana_la_carrera_se_adopta_el_suyo(tmp_path: Path, monkeypatch) -> None:
    """Otro proceso crea el fichero entre nuestro is_file() y el link: se
    adopta su token (FileExistsError), no se pisa."""
    path = tmp_path / "token"
    real_link = os.link

    def _rival_first(src, dst, *a, **kw):
        Path(dst).write_text("token-del-rival")
        os.chmod(dst, 0o600)
        real_link(src, dst, *a, **kw)  # -> FileExistsError

    monkeypatch.setattr(display_ws.os, "link", _rival_first)
    assert _load_or_create_token(path) == "token-del-rival"


def test_token_race_o_excl_lector_espera_al_escritor(tmp_path: Path, monkeypatch) -> None:
    """Issue #115, ruta de fallback (sistemas sin hard links): el ganador de
    O_EXCL crea el inode vacío y se suspende antes de escribir. Un segundo
    lector no puede devolver '' ni regenerar encima: espera y adopta el token
    del ganador."""
    path = tmp_path / "token"
    path.write_text("")  # el ganador ya hizo os.open(O_EXCL) pero no write()
    os.chmod(path, 0o600)

    def _winner_writes_later() -> None:
        threading.Event().wait(0.05)
        path.write_text("token-del-ganador")

    t = threading.Thread(target=_winner_writes_later)
    t.start()
    monkeypatch.setattr(display_ws, "_EMPTY_TOKEN_WAIT_S", 2.0)
    try:
        assert _load_or_create_token(path) == "token-del-ganador"
    finally:
        t.join()


def test_token_fallback_sin_hard_links(tmp_path: Path, monkeypatch) -> None:
    def _no_links(src, dst, *a, **kw):
        raise PermissionError("link not permitted")

    monkeypatch.setattr(display_ws.os, "link", _no_links)
    path = tmp_path / "token"
    token = _load_or_create_token(path)
    assert path.read_text() == token and len(token) == 64
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["token"]


@pytest.mark.asyncio
async def test_start_falla_cerrado_si_no_hay_token(tmp_path: Path, monkeypatch) -> None:
    """Defensa en profundidad (#115): sin token no vacío no se sirve."""
    monkeypatch.setattr(display_ws, "_load_or_create_token", lambda path: "")
    backend = WsDisplayBackend(_cfg(tmp_path))
    await backend.start()
    try:
        assert backend.port is None
    finally:
        await backend.stop()


# ---------------------------------------------------------------------------
# Handshake: auth, rate limit (#106), límite de clientes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "token-equivocado", ""])
async def test_handshake_sin_token_valido_es_401(server, token) -> None:
    backend, _, _ = server
    with pytest.raises(websockets.exceptions.InvalidStatus) as exc:
        await _connect(backend, token)
    assert exc.value.response.status_code == 401


@pytest.mark.asyncio
async def test_rate_limit_solo_cuenta_tokens_invalidos(tmp_path: Path) -> None:
    """Issue #106: una riada de tokens inválidos (escaneo desde otra pestaña
    o proceso) recibe 429, pero el cliente legítimo sigue conectando."""
    backend = WsDisplayBackend(_cfg(tmp_path, rate_limit_max_requests=2))
    await backend.start()
    try:
        token = (tmp_path / "token").read_text().strip()
        codes = []
        for _ in range(5):
            with pytest.raises(websockets.exceptions.InvalidStatus) as exc:
                await _connect(backend, "malo")
            codes.append(exc.value.response.status_code)
        assert codes == [401, 401, 429, 429, 429]
        ws = await _connect(backend, token)
        try:
            assert (await _recv(ws))["type"] == "sync"
        finally:
            await ws.close()
    finally:
        await backend.stop()


@pytest.mark.asyncio
async def test_clientes_legitimos_no_consumen_rate_limit(tmp_path: Path) -> None:
    backend = WsDisplayBackend(_cfg(tmp_path, rate_limit_max_requests=1))
    await backend.start()
    try:
        token = (tmp_path / "token").read_text().strip()
        for _ in range(4):  # reconexiones legítimas repetidas
            ws = await _connect(backend, token)
            await _recv(ws)
            await ws.close()
    finally:
        await backend.stop()


@pytest.mark.asyncio
async def test_max_clients_rechaza_con_503(tmp_path: Path) -> None:
    backend = WsDisplayBackend(_cfg(tmp_path, max_clients=2))
    await backend.start()
    try:
        token = (tmp_path / "token").read_text().strip()
        a = await _connect(backend, token)
        b = await _connect(backend, token)
        await _recv(a)
        await _recv(b)
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc:
            await _connect(backend, token)
        assert exc.value.response.status_code == 503
        await a.close()
        await wait_until(lambda: len(backend._clients) == 1, message="a desconectado")
        c = await _connect(backend, token)
        await _recv(c)
        await b.close()
        await c.close()
    finally:
        await backend.stop()


@pytest.mark.asyncio
async def test_puerto_ocupado_no_crashea(tmp_path: Path) -> None:
    blocker = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    busy = blocker.sockets[0].getsockname()[1]
    backend = WsDisplayBackend(_cfg(tmp_path, port=busy))
    try:
        await asyncio.wait_for(backend.start(), timeout=2.0)  # no lanza
        assert backend.port is None
        backend.send("state", {"value": "listening", "text": ""})  # no-op seguro
    finally:
        blocker.close()
        await blocker.wait_closed()
        await backend.stop()


@pytest.mark.asyncio
async def test_escucha_solo_en_loopback(server) -> None:
    backend, _, _ = server
    hosts = {s.getsockname()[0] for s in backend._server.sockets}
    assert hosts == {"127.0.0.1"}


# ---------------------------------------------------------------------------
# sync al conectar + broadcast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_primer_mensaje_es_sync_con_version(server) -> None:
    backend, token, _ = server
    ws = await _connect(backend, token)
    try:
        msg = await _recv(ws)
    finally:
        await ws.close()
    assert msg["type"] == "sync"
    assert msg["protocol_version"] == PROTOCOL_VERSION == 1
    assert msg["data"] == {"state": "idle", "text": "", "turn_id": None, "health": {}}
    assert isinstance(msg["seq"], int) and isinstance(msg["ts"], float)


@pytest.mark.asyncio
async def test_broadcast_envelope(server) -> None:
    backend, token, _ = server
    ws = await _connect(backend, token)
    try:
        sync = await _recv(ws)
        backend.send("state", {"value": "listening", "text": ""})
        msg = await _recv(ws)
    finally:
        await ws.close()
    assert msg["type"] == "state"
    assert msg["data"] == {"value": "listening", "text": ""}
    assert msg["seq"] == sync["seq"] + 1
    assert msg["turn_id"] is None


@pytest.mark.asyncio
async def test_reconexion_recibe_snapshot_actualizado(server) -> None:
    """Idempotencia: quien conecta tarde no necesita histórico."""
    backend, token, _ = server
    backend.send("turn_start", {"turn_id": "t-7", "turn_seq": 7})
    backend.send("state", {"value": "thinking", "text": "hola"})
    backend.send("text_update", {
        "chunk_text": "Buenos días", "cumulative_text": "Buenos días", "duration_s": 0.4,
    })
    ws = await _connect(backend, token)
    try:
        msg = await _recv(ws)
    finally:
        await ws.close()
    assert msg["type"] == "sync"
    assert msg["turn_id"] == "t-7"
    assert msg["data"]["state"] == "response"
    assert msg["data"]["text"] == "Buenos días"
    assert msg["data"]["turn_id"] == "t-7"


@pytest.mark.asyncio
async def test_idle_limpia_turn_id_y_texto(server) -> None:
    backend, token, _ = server
    backend.send("turn_start", {"turn_id": "t-1"})
    backend.send("text_update", {"chunk_text": "x", "cumulative_text": "x", "duration_s": 0.1})
    backend.send("state", {"value": "idle", "text": ""})
    ws = await _connect(backend, token)
    try:
        msg = await _recv(ws)
    finally:
        await ws.close()
    assert msg["data"]["state"] == "idle"
    assert msg["data"]["text"] == ""
    assert msg["turn_id"] is None


@pytest.mark.asyncio
async def test_varios_displays_reciben_lo_mismo(server) -> None:
    backend, token, _ = server
    a = await _connect(backend, token)
    b = await _connect(backend, token)
    try:
        await _recv(a)
        await _recv(b)
        backend.send("turn_start", {"turn_id": "t-2", "turn_seq": 2})
        backend.send("tool_call", {
            "phase": "start", "name": "exec", "tool_call_id": "c-1",
            "args": {"command": "ls"}, "result": None, "is_error": None,
        })
        for ws in (a, b):
            ts = await _recv(ws)
            tc = await _recv(ws)
            assert ts["type"] == "turn_start" and ts["turn_id"] == "t-2"
            assert ts["data"] == {"turn_id": "t-2", "turn_seq": 2}
            assert tc["type"] == "tool_call" and tc["turn_id"] == "t-2"
            assert tc["data"]["tool_call_id"] == "c-1"
    finally:
        await a.close()
        await b.close()


@pytest.mark.asyncio
async def test_text_update_payload(server) -> None:
    backend, token, _ = server
    ws = await _connect(backend, token)
    try:
        await _recv(ws)
        backend.send("text_update", {"chunk_text": "Hola", "cumulative_text": "Hola", "duration_s": 0.3})
        msg = await _recv(ws)
    finally:
        await ws.close()
    assert msg["type"] == "text_update"
    assert msg["data"] == {"chunk_text": "Hola", "cumulative_text": "Hola", "duration_s": 0.3}


@pytest.mark.asyncio
async def test_error_y_cancelled_son_eventos_no_estado(server) -> None:
    backend, token, _ = server
    backend.send("state", {"value": "response", "text": "a"})
    backend.send("error", {"code": "TURN_ERROR", "message": "boom", "fatal": False})
    backend.send("cancelled", {"state": "RESPONDING"})
    ws = await _connect(backend, token)
    try:
        msg = await _recv(ws)
    finally:
        await ws.close()
    assert msg["data"]["state"] == "response"


@pytest.mark.asyncio
async def test_tipo_desconocido_no_se_emite(server) -> None:
    backend, token, _ = server
    ws = await _connect(backend, token)
    try:
        await _recv(ws)
        backend.send("inventado", {"x": 1})
        backend.send("state", {"value": "listening", "text": ""})
        assert (await _recv(ws))["type"] == "state"
    finally:
        await ws.close()


# ---------------------------------------------------------------------------
# health: estado informativo con caducidad (parte display de #7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_entra_en_sync_y_ok_lo_limpia(server) -> None:
    backend, token, _ = server
    backend.send("health", {"key": "oww", "level": "warning", "service": "oww",
                            "state": "degraded", "ttl_s": None})
    ws = await _connect(backend, token)
    try:
        sync = await _recv(ws)
        assert sync["data"]["health"]["oww"]["level"] == "warning"
        backend.send("health", {"key": "oww", "level": "ok", "service": "oww",
                                "state": "recovered", "ttl_s": None})
        msg = await _recv(ws)
        assert msg["type"] == "health" and msg["data"]["level"] == "ok"
    finally:
        await ws.close()
    ws = await _connect(backend, token)
    try:
        assert (await _recv(ws))["data"]["health"] == {}
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_health_con_ttl_caduca_sola_y_se_anuncia(server) -> None:
    """jota-gateway#227: llegan `transcriber degraded` falsos en reposo. Un
    aviso con ttl se limpia solo y el servidor emite el `ok` de caducidad."""
    backend, token, _ = server
    ws = await _connect(backend, token)
    try:
        await _recv(ws)
        backend.send("health", {"key": "gateway.transcriber", "level": "info",
                                "service": "transcriber", "state": "degraded", "ttl_s": 0.05})
        first = await _recv(ws)
        assert first["data"]["level"] == "info"
        expired = await _recv(ws)
        assert expired["type"] == "health"
        assert expired["data"]["key"] == "gateway.transcriber"
        assert expired["data"]["level"] == "ok"
        assert expired["data"]["state"] == "expired"
    finally:
        await ws.close()
    assert backend._snapshot_health() == {}


@pytest.mark.asyncio
async def test_health_renovado_reinicia_el_ttl(server) -> None:
    backend, _, _ = server
    for _ in range(3):
        backend.send("health", {"key": "k", "level": "info", "ttl_s": 0.08})
        await asyncio.sleep(0.04)
    assert "k" in backend._snapshot_health()
    await wait_until(lambda: "k" not in backend._snapshot_health(), message="caduca")


# ---------------------------------------------------------------------------
# Comandos display -> servidor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_invoca_on_cancel(server) -> None:
    backend, token, cancels = server
    ws = await _connect(backend, token)
    try:
        await _recv(ws)
        await ws.send(json.dumps({"type": "cancel"}))
        await wait_until(lambda: len(cancels) == 1, message="cancel")
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_cancel_de_un_turno_ya_terminado_se_ignora(server) -> None:
    """Idempotencia: un cancel con el turn_id de un turno viejo (llegado
    tarde o reenviado al reconectar) no cancela el turno nuevo."""
    backend, token, cancels = server
    backend.send("turn_start", {"turn_id": "t-nuevo"})
    ws = await _connect(backend, token)
    try:
        await _recv(ws)
        await ws.send(json.dumps({"type": "cancel", "turn_id": "t-viejo"}))
        await ws.send(json.dumps({"type": "cancel", "turn_id": "t-nuevo"}))
        await wait_until(lambda: len(cancels) == 1, message="solo el bueno")
        await asyncio.sleep(0.02)
        assert len(cancels) == 1
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_resync_reenvia_sync(server) -> None:
    backend, token, _ = server
    ws = await _connect(backend, token)
    try:
        await _recv(ws)
        backend.send("state", {"value": "listening", "text": ""})
        await _recv(ws)
        await ws.send(json.dumps({"type": "resync"}))
        msg = await _recv(ws)
        assert msg["type"] == "sync"
        assert msg["data"]["state"] == "listening"
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_comandos_invalidos_no_rompen_la_conexion(server) -> None:
    backend, token, cancels = server
    ws = await _connect(backend, token)
    try:
        await _recv(ws)
        await ws.send(json.dumps({"type": "algo_no_soportado"}))
        await ws.send("no-es-json-{{{")
        await ws.send(json.dumps([1, 2]))
        await ws.send(b"\x00\x01")
        backend.send("state", {"value": "listening", "text": ""})
        assert (await _recv(ws))["data"]["value"] == "listening"
        assert cancels == []
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_mensaje_entrante_grande_cierra_la_conexion(server) -> None:
    backend, token, _ = server
    ws = await _connect(backend, token)
    try:
        await _recv(ws)
        await ws.send("x" * 100_000)
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            await asyncio.wait_for(ws.recv(), timeout=2.0)
    finally:
        await ws.close()


# ---------------------------------------------------------------------------
# Issue #64: un display lento no frena el pipeline ni a los demás displays
# ---------------------------------------------------------------------------


class _StuckConnection:
    """Conexión cuyo send() nunca termina (display colgado)."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.blocked = asyncio.Event()

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))
        self.blocked.set()
        await asyncio.Event().wait()


class _FastConnection:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


@pytest.mark.asyncio
async def test_display_colgado_no_bloquea_send_ni_a_otros_displays(tmp_path: Path) -> None:
    backend = WsDisplayBackend(_cfg(tmp_path))
    stuck, fast = _StuckConnection(), _FastConnection()
    slow_client = backend._register(stuck)
    fast_client = backend._register(fast)
    try:
        await asyncio.wait_for(stuck.blocked.wait(), 2.0)  # atascado en el sync
        cumulative = ""
        for i in range(2000):  # send() es síncrono: nunca espera al display
            cumulative += "x"
            backend.send("text_update", {"chunk_text": "x", "cumulative_text": cumulative,
                                         "duration_s": 0.01})
            if i % 100 == 99:
                await asyncio.sleep(0)  # los eventos llegan del bus a ráfagas
        # La cola del display lento está acotada…
        assert slow_client.queue.qsize() <= backend.CLIENT_QUEUE_MAX
        # …y tras desbordar contiene un sync fresco para resincronizar.
        pending = [slow_client.queue.get_nowait() for _ in range(slow_client.queue.qsize())]
        assert any(m["type"] == "sync" for m in pending)
        last = pending[-1]["data"]
        assert last.get("cumulative_text", last.get("text")) == cumulative
        # El display sano lo recibe todo.
        await wait_until(lambda: len(fast.sent) == 2001, message="display sano al día")
        assert fast.sent[-1]["data"]["cumulative_text"] == cumulative
    finally:
        await backend._unregister(slow_client)
        await backend._unregister(fast_client)


@pytest.mark.asyncio
async def test_seq_monotono_y_sync_tras_hueco(tmp_path: Path) -> None:
    backend = WsDisplayBackend(_cfg(tmp_path))
    backend.CLIENT_QUEUE_MAX = 4
    stuck = _StuckConnection()
    client = backend._register(stuck)
    try:
        await asyncio.wait_for(stuck.blocked.wait(), 2.0)
        for i in range(10):
            backend.send("state", {"value": "listening", "text": str(i)})
        msgs = [client.queue.get_nowait() for _ in range(client.queue.qsize())]
        seqs = [m["seq"] for m in msgs]
        assert seqs == sorted(seqs)
        assert msgs[0]["type"] == "sync"  # el hueco siempre lo tapa un sync
    finally:
        await backend._unregister(client)


# ---------------------------------------------------------------------------
# Extremo a extremo: EventBus -> DisplayClient -> WsDisplayBackend -> display
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_bus_display_client_y_cancel(tmp_path: Path) -> None:
    from app.display_client import DisplayClient
    from domain.event_bus import EventBus, VoiceEvent

    cancel_event = asyncio.Event()
    backend = WsDisplayBackend(_cfg(tmp_path), on_cancel=cancel_event.set)
    await backend.start()
    bus = EventBus()
    task = asyncio.create_task(DisplayClient(backend).run(bus))
    try:
        ws = await _connect(backend, (tmp_path / "token").read_text().strip())
        try:
            assert (await _recv(ws))["type"] == "sync"
            bus.publish(VoiceEvent(type="recording_started", data={}))
            bus.publish(VoiceEvent(type="turn_start", data={"turn_id": "t-9", "turn_seq": 9}))
            bus.publish(VoiceEvent(type="display_text_update", data={
                "chunk_text": "Hola", "cumulative_text": "Hola", "duration_s": 0.2}))
            got = [await _recv(ws) for _ in range(3)]
            assert [m["type"] for m in got] == ["state", "turn_start", "text_update"]
            assert got[2]["turn_id"] == "t-9"
            await ws.send(json.dumps({"type": "cancel", "turn_id": "t-9"}))
            await asyncio.wait_for(cancel_event.wait(), 2.0)
        finally:
            await ws.close()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await backend.stop()
