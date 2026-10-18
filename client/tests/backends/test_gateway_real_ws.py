"""Fase R (#118, #116, #107): GatewayClient contra un servidor WebSocket real.

Servidor fake local con ``websockets.serve`` en 127.0.0.1 (puerto 0) que
imita lo que hace jota-gateway en el wire: handshake JSON → ``status``
opcional → ``ready``, y luego cierra con distintos códigos o corta el TCP
sin close frame. Sin mocks de la librería: valida que las excepciones y el
iterador reales de websockets se traducen bien.
"""
from __future__ import annotations

import asyncio
import json

import pytest
import websockets
from websockets.asyncio.server import serve

from backends.gateway_client import (
    GatewayClient,
    GatewayDisconnected,
    GatewayHandshakeError,
)
from config import GatewayConfig
from domain import gateway_session
from domain.event_bus import EventBus


class FakeGatewayServer:
    """``plan`` es una lista de acciones por conexión (la última se repite):
    ("ready", then) donde then es None (mantener abierta), ("close", code,
    reason, after_s), ("abort", after_s) o ("flood", n_bytes, chunk);
    ("reject", code, reason) cierra en el handshake."""

    def __init__(self, plan):
        self.plan = list(plan)
        self.connections = 0
        self.handshakes: list[dict] = []
        self.received_text: list[str] = []
        self.server = None
        self.port = None

    async def __aenter__(self):
        self.server = await serve(self._handler, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws/stream"

    async def _handler(self, ws):
        idx = min(self.connections, len(self.plan) - 1)
        self.connections += 1
        action = self.plan[idx]
        hs = json.loads(await ws.recv())
        self.handshakes.append(hs)
        if action[0] == "reject":
            await ws.close(action[1], action[2])
            return
        await ws.send(json.dumps({"type": "status", "service": "transcriber", "state": "unavailable"}))
        await ws.send(json.dumps({
            "type": "ready", "session_id": f"c:{self.connections}", "agent": "main",
            "requested_capabilities": {"tts": True}, "live_capabilities": {"tts": True},
        }))
        then = action[1] if len(action) > 1 else None
        reader = asyncio.create_task(self._read(ws))
        try:
            if then is None:
                await ws.wait_closed()
            elif then[0] == "close":
                await asyncio.sleep(then[3])
                await ws.close(then[1], then[2])
            elif then[0] == "abort":
                await asyncio.sleep(then[1])
                ws.transport.abort()  # corte sin close frame → 1006 en el cliente
            elif then[0] == "flood":
                _, total, chunk = then
                frame = bytes([0xA1, 0x00, 0x01]) + b"\x00" * chunk
                for _ in range(total // chunk):
                    await ws.send(frame)
                await ws.send(json.dumps({"type": "turn_end", "turn_id": "t-1"}))
                await ws.wait_closed()
        finally:
            reader.cancel()

    async def _read(self, ws):
        try:
            async for msg in ws:
                if isinstance(msg, str):
                    self.received_text.append(msg)
        except Exception:
            pass


def _cfg(url: str, **kw) -> GatewayConfig:
    base = dict(
        url=url, client_key="k", connect_timeout_s=2.0,
        reconnect_backoff_s=[0.05, 0.1], keepalive_interval_s=0,
    )
    base.update(kw)
    return GatewayConfig(**base)


async def _wait_for(pred, timeout: float = 3.0) -> bool:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return pred()


async def _supervise(cfg, gw, bus=None):
    return asyncio.create_task(gateway_session.idle_supervisor(cfg, bus or EventBus(), gw))


async def _stop(task):
    task.cancel()
    await asyncio.wait([task])


@pytest.mark.asyncio
async def test_real_status_antes_de_ready_y_capabilities():
    async with FakeGatewayServer([("ready", None)]) as srv:
        gw = GatewayClient(_cfg(srv.url))
        await gw.connect()
        assert gw.is_connected
        assert gw._session_id == "c:1"
        assert gw._capabilities["live"] == {"tts": True}
        assert srv.handshakes[0]["client_key"] == "k"
        await gw.disconnect()


@pytest.mark.parametrize("code", [1000, 1001, 1012])
@pytest.mark.asyncio
async def test_real_cierre_ordenado_en_idle_reconecta(code, monkeypatch):
    """1000 = IDLE_TIMEOUT_S del gateway (sin aviso); 1001/1012 = apagado o
    reinicio. El iterador real de websockets termina SIN excepción con
    1000/1001: antes eso dejaba un socket muerto "conectado"."""
    monkeypatch.setattr(gateway_session, "_STABLE_SESSION_S", 0.0)
    async with FakeGatewayServer([("ready", ("close", code, "bye", 0.1)), ("ready", None)]) as srv:
        gw = GatewayClient(_cfg(srv.url))
        await gw.connect()
        sup = await _supervise(gw._cfg, gw)
        assert await _wait_for(lambda: srv.connections == 2 and gw.is_connected)
        assert gw.last_close.code == code
        assert gw._session_id == "c:2"
        await _stop(sup)
        await gw.disconnect()


@pytest.mark.asyncio
async def test_real_corte_sin_close_frame_reconecta():
    async with FakeGatewayServer([("ready", ("abort", 0.1)), ("ready", None)]) as srv:
        gw = GatewayClient(_cfg(srv.url))
        await gw.connect()
        sup = await _supervise(gw._cfg, gw)
        assert await _wait_for(lambda: srv.connections == 2 and gw.is_connected)
        assert gw.last_close.code == 1006
        await _stop(sup)
        await gw.disconnect()


@pytest.mark.asyncio
async def test_real_rechazo_1008_tipado_y_sin_bucle():
    async with FakeGatewayServer([("reject", 1008, "Clave de cliente invalida o inactiva")]) as srv:
        gw = GatewayClient(_cfg(srv.url))
        with pytest.raises(GatewayHandshakeError) as info:
            await gw.connect()
        assert info.value.kind == "invalid_client_key" and info.value.fatal
        assert not gw.is_connected
        sup = await _supervise(gw._cfg, gw)
        await asyncio.sleep(0.4)
        await _stop(sup)
        assert srv.connections == 1, "no debe reintentar un 1008 en segundo plano"


@pytest.mark.asyncio
async def test_real_agent_not_available_se_distingue():
    async with FakeGatewayServer([("reject", 1008, "Agent 'main' not available.")]) as srv:
        gw = GatewayClient(_cfg(srv.url))
        with pytest.raises(GatewayHandshakeError) as info:
            await gw.connect()
        assert info.value.kind == "agent_not_available" and not info.value.fatal


@pytest.mark.asyncio
async def test_real_cierre_a_mitad_de_turno_lanza_gateway_disconnected():
    """Un 1001 mientras se lee la respuesta ya no termina el turno "bien"."""
    async with FakeGatewayServer([("ready", ("close", 1001, "shutdown", 0.1))]) as srv:
        gw = GatewayClient(_cfg(srv.url))
        await gw.connect()
        with pytest.raises(GatewayDisconnected) as info:
            async for _ in gw.receive():
                pass
        assert info.value.code == 1001
        assert not gw.is_connected


@pytest.mark.asyncio
async def test_real_keepalive_de_aplicacion_llega_al_gateway():
    """#116: el gateway solo cuenta mensajes del cliente; el keepalive es un
    JSON que su bucle de entrada ignora pero que resetea el contador."""
    async with FakeGatewayServer([("ready", None)]) as srv:
        gw = GatewayClient(_cfg(srv.url, keepalive_interval_s=0.1))
        await gw.connect()
        sup = await _supervise(gw._cfg, gw)
        assert await _wait_for(
            lambda: any(json.loads(m).get("type") == "keepalive" for m in srv.received_text), 2.0)
        await _stop(sup)
        await gw.disconnect()


@pytest.mark.asyncio
async def test_real_lector_lento_no_corta_por_ping():
    """#118 (medición del 2026-10-11): con max_queue por defecto, leer al
    ritmo de reproducción dejaba el pong detrás del audio y la propia
    librería cerraba (1011) al vencer ping_timeout. Escalado: ping cada
    0.2 s, timeout 0.2 s, 3 MB enviados de golpe y lectura lenta > 1.5 s."""
    async with FakeGatewayServer([("ready", ("flood", 3_000_000, 60_000))]) as srv:
        gw = GatewayClient(_cfg(srv.url, ping_interval_s=0.2, ping_timeout_s=0.2))
        await gw.connect()
        n = 0
        async for ev in gw.receive():
            n += 1
            await asyncio.sleep(0.03)  # "reproduce" cada fragmento
        assert n == 50
        assert gw.is_connected, f"la librería cerró la conexión: {gw.last_close}"
        await gw.disconnect()


@pytest.mark.asyncio
async def test_real_control_max_queue_por_defecto_si_corta():
    """Control del anterior: mismo escenario con la cola por defecto (16)
    → la librería da la conexión por muerta. Confirma el diagnóstico de
    #118 y que el fix es max_queue=None, no casualidad."""
    async with FakeGatewayServer([("ready", ("flood", 3_000_000, 60_000))]) as srv:
        ws = await websockets.connect(srv.url, max_size=None, ping_interval=0.2, ping_timeout=0.2)
        await ws.send(json.dumps({"client_key": "k", "input_mode": "audio", "output_mode": ["audio"]}))
        closed = False

        async def _read_slowly():
            async for _ in ws:
                await asyncio.sleep(0.03)

        try:
            # Acotado: si la librería no llegara a cortar, falla este test en
            # vez de colgar toda la ejecución de pytest.
            await asyncio.wait_for(_read_slowly(), 5.0)
        except websockets.exceptions.ConnectionClosed:
            closed = True
        except asyncio.TimeoutError:
            await ws.close()
        assert closed or ws.close_code not in (None, 1000), (
            "con max_queue=16 la librería debía cortar por keepalive"
        )
