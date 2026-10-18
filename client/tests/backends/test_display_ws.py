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
from pathlib import Path

import pytest
import pytest_asyncio
import websockets

from backends import display_ws
from backends.display_ws import (
    PROTOCOL_VERSION,
    TOKEN_HEADER,
    WsDisplayBackend,
    _rotate_token,
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
# Token: fichero 0600 que se ROTA en cada start() (revisión Fase D, #115)
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


@pytest.mark.asyncio
async def test_token_rota_en_cada_start(tmp_path: Path) -> None:
    """El token da acceso a transcripciones, respuestas y tool calls: un
    token filtrado (p.ej. a quien ocupó el puerto con jota-voice parado)
    deja de valer en el siguiente arranque."""
    seen = []
    for _ in range(2):
        backend = WsDisplayBackend(_cfg(tmp_path))
        await backend.start()
        try:
            token = (tmp_path / "token").read_text().strip()
            seen.append(token)
            ws = await _connect(backend, token)
            assert (await _recv(ws))["type"] == "sync"
            await ws.close()
        finally:
            await backend.stop()
    assert seen[0] != seen[1]
    assert all(len(t) == 64 for t in seen)


@pytest.mark.asyncio
async def test_token_viejo_deja_de_valer_tras_reiniciar(tmp_path: Path) -> None:
    first = WsDisplayBackend(_cfg(tmp_path))
    await first.start()
    old = (tmp_path / "token").read_text().strip()
    await first.stop()
    second = WsDisplayBackend(_cfg(tmp_path))
    await second.start()
    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc:
            await _connect(second, old)
        assert exc.value.response.status_code == 401
    finally:
        await second.stop()


@pytest.mark.parametrize("contenido,modo", [("token-viejo", 0o644), ("", 0o600), ("  \n", 0o666)])
def test_rotar_sustituye_cualquier_fichero_previo_y_deja_600(
    tmp_path: Path, contenido: str, modo: int
) -> None:
    path = tmp_path / "token"
    path.write_text(contenido)
    os.chmod(path, modo)
    token = _rotate_token(path)
    assert len(token) == 64 and token != contenido.strip()
    assert path.read_text() == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_rotar_nunca_deja_el_fichero_vacio_ni_temporales(tmp_path: Path, monkeypatch) -> None:
    """#115: el token se escribe en un temporal 0600 y se publica con
    os.replace (atómico): un lector ve el token viejo completo o el nuevo
    completo, nunca un fichero vacío."""
    path = tmp_path / "token"
    path.write_text("token-viejo")
    os.chmod(path, 0o600)
    seen: list[tuple[str, str]] = []
    real_replace = os.replace

    def _spy_replace(src, dst, *a, **kw):
        seen.append((Path(src).read_text(), Path(dst).read_text()))
        real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(display_ws.os, "replace", _spy_replace)
    token = _rotate_token(path)
    assert seen == [(token, "token-viejo")]
    assert [p.name for p in tmp_path.iterdir()] == ["token"]


def test_rotar_crea_el_directorio_con_0700(tmp_path: Path) -> None:
    path = tmp_path / "nuevo" / "token"
    _rotate_token(path)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_rotar_limpia_el_temporal_si_falla(tmp_path: Path, monkeypatch) -> None:
    def _boom(src, dst, *a, **kw):
        raise OSError("disco lleno")

    monkeypatch.setattr(display_ws.os, "replace", _boom)
    with pytest.raises(OSError):
        _rotate_token(tmp_path / "token")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_start_falla_cerrado_si_no_hay_token(tmp_path: Path, monkeypatch) -> None:
    """Defensa en profundidad (#115): sin token no vacío no se sirve."""
    monkeypatch.setattr(display_ws, "_rotate_token", lambda path: "")
    backend = WsDisplayBackend(_cfg(tmp_path))
    await backend.start()
    try:
        assert backend.port is None
    finally:
        await backend.stop()


@pytest.mark.asyncio
async def test_start_falla_cerrado_si_no_se_puede_escribir_el_token(tmp_path: Path, monkeypatch) -> None:
    def _boom(path):
        raise OSError("sin permisos")

    monkeypatch.setattr(display_ws, "_rotate_token", _boom)
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
async def test_cabecera_de_token_duplicada_es_401_y_cuenta_en_el_limiter(tmp_path: Path, caplog) -> None:
    """Revisión Fase D: dos X-Jota-Display-Token daban 500 con traceback
    (MultipleValuesError) y no pasaban por el rate limit."""
    import logging

    backend = WsDisplayBackend(_cfg(tmp_path, rate_limit_max_requests=1))
    await backend.start()
    try:
        token = (tmp_path / "token").read_text().strip()
        headers = [(TOKEN_HEADER, token), (TOKEN_HEADER, token)]
        codes = []
        with caplog.at_level(logging.ERROR):
            for _ in range(2):
                with pytest.raises(websockets.exceptions.InvalidStatus) as exc:
                    await websockets.connect(f"ws://127.0.0.1:{backend.port}",
                                             additional_headers=headers, open_timeout=2.0)
                codes.append(exc.value.response.status_code)
        assert codes == [401, 429]
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    finally:
        await backend.stop()


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


HEALTH_FIELDS = {"key", "level", "service", "state", "ttl_s", "detail"}  # protocolo §5/§13


@pytest.mark.asyncio
async def test_todos_los_health_emitidos_cumplen_el_protocolo(tmp_path: Path) -> None:
    """Revisión Fase D: el health de caducidad no llevaba `detail`."""
    from app.display_client import DisplayClient
    from domain.event_bus import VoiceEvent

    backend = WsDisplayBackend(_cfg(tmp_path))
    conn = _FastConnection()
    client = backend._register(conn)
    dc = DisplayClient(backend)
    try:
        for ev in [
            VoiceEvent(type="gateway_status", data={"service": "transcriber", "state": "degraded"}),
            VoiceEvent(type="gateway_status", data={"service": "tts", "state": "unavailable"}),
            VoiceEvent(type="gateway_status", data={"service": "tts", "state": "restored"}),
            VoiceEvent(type="gateway_status", data={"service": "gateway", "state": "disconnected",
                                                    "code": 1006, "reason": ""}),
            VoiceEvent(type="gateway_status", data={"service": "gateway", "state": "connected"}),
            VoiceEvent(type="oww_degraded", data={"consecutive_failures": 3}),
            VoiceEvent(type="oww_recovered", data={}),
        ]:
            dc._handle(ev)
        # Caducidad del aviso degraded (ttl corto para el test).
        backend.send("health", {"key": "k", "level": "info", "service": "transcriber",
                                "state": "degraded", "ttl_s": 0.02, "detail": {}})
        await wait_until(
            lambda: any(m["type"] == "health" and m["data"]["state"] == "expired"
                        for m in conn.sent),
            message="health de caducidad",
        )
        healths = [m["data"] for m in conn.sent if m["type"] == "health"]
        assert len(healths) == 9
        for h in healths:
            assert set(h) == HEALTH_FIELDS, h
            assert h["level"] in {"ok", "info", "warning"}
            assert isinstance(h["detail"], dict)
            assert h["ttl_s"] is None or h["ttl_s"] > 0
    finally:
        await backend._unregister(client)


@pytest.mark.asyncio
async def test_health_renovado_reinicia_el_ttl(server) -> None:
    """Renovar un health cancela su temporizador y arma otro. Antes se
    renovaba cada 40 ms un TTL de 80 ms con sleeps reales: con la máquina
    cargada caducaba entre renovaciones sin bug (issue #76). Ahora se miran
    los temporizadores, y la caducidad real con un TTL corto."""
    backend, _, _ = server
    backend.send("health", {"key": "k", "level": "info", "ttl_s": 60})
    first = backend._health_timers["k"]
    backend.send("health", {"key": "k", "level": "info", "ttl_s": 60})
    second = backend._health_timers["k"]
    assert second is not first
    assert first.cancelled(), "la renovación debe cancelar el TTL anterior"
    assert not second.cancelled()
    assert "k" in backend._snapshot_health()
    # La última renovación manda: con TTL corto caduca de verdad.
    backend.send("health", {"key": "k", "level": "info", "ttl_s": 0.01})
    assert second.cancelled()
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
        # El servidor procesa los mensajes de un display en orden: cuando
        # llega el efecto del segundo, el primero ya se descartó. Sin sleep
        # de "por si acaso" (issue #76).
        await wait_until(lambda: len(cancels) == 1, message="solo el bueno")
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


@pytest.mark.asyncio
async def test_stop_es_rapido_con_un_display_que_no_lee(tmp_path: Path) -> None:
    """Revisión Fase D: un display que deja de leer tras el handshake hacía
    que stop() tardara ~20 s (close_timeout 10 s + wait_closed), más de lo
    que launchd espera antes de matar el proceso."""
    import time

    backend = WsDisplayBackend(_cfg(tmp_path))
    await backend.start()
    token = (tmp_path / "token").read_text().strip().encode()
    r, w = await asyncio.open_connection("127.0.0.1", backend.port, limit=1024)
    try:
        w.transport.set_write_buffer_limits(0)
        w.write(
            b"GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n"
            b"X-Jota-Display-Token: " + token + b"\r\n\r\n"
        )
        await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 2.0)
        w.transport.pause_reading()  # no vuelve a leer nunca
        big = "x" * 20000
        for i in range(3000):
            backend.send("text_update", {"chunk_text": "a", "cumulative_text": big + str(i),
                                         "duration_s": 0.1})
            if i % 100 == 0:
                await asyncio.sleep(0)
        await asyncio.sleep(0.1)  # setup: deja que se llenen los buffers del socket
        t0 = time.monotonic()
        await asyncio.wait_for(backend.stop(), 30.0)
        # El bug tardaba ~20 s (close_timeout 10 s + wait_closed); el fix,
        # ~CLOSE_TIMEOUT_S + STOP_TIMEOUT_S (2 s). Margen amplio para no
        # fallar por carga (issue #76) sin dejar de detectar el bug.
        assert time.monotonic() - t0 < 8.0
        assert backend._clients == set()
    finally:
        w.close()


@pytest.mark.asyncio
async def test_writer_que_falla_cierra_la_conexion_para_forzar_reconexion(tmp_path: Path) -> None:
    class _Broken:
        def __init__(self) -> None:
            self.closed: list[int] = []

        async def send(self, raw: str) -> None:
            raise RuntimeError("socket raro")

        async def close(self, code: int = 1000, reason: str = "") -> None:
            self.closed.append(code)

    backend = WsDisplayBackend(_cfg(tmp_path))
    conn = _Broken()
    client = backend._register(conn)
    try:
        await wait_until(lambda: conn.closed == [1011], message="cerrada con 1011")
    finally:
        await backend._unregister(client)


@pytest.mark.asyncio
async def test_valores_no_serializables_no_matan_al_writer(tmp_path: Path) -> None:
    backend = WsDisplayBackend(_cfg(tmp_path))
    fast = _FastConnection()
    client = backend._register(fast)
    try:
        backend.send("health", {"key": "x", "level": "warning", "detail": {"obj": object()}})
        backend.send("state", {"value": "listening", "text": ""})
        await wait_until(lambda: len(fast.sent) == 3, message="sync + 2")
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
