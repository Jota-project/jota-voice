"""Tests de GatewayClient — no requieren servidor real."""
from __future__ import annotations

import asyncio
import json
import warnings
from unittest.mock import AsyncMock, create_autospec

import pytest

# Antes este fichero sustituía websockets.exceptions.ConnectionClosed* por
# clases fake a nivel de módulo (y websockets.connect por un AsyncMock),
# para TODA la sesión de pytest: cualquier test posterior con un servidor
# real veía excepciones que el código ya no reconocía. Ahora se usan las
# excepciones reales (construidas con frames Close) y websockets.connect se
# sustituye solo durante cada test con el fixture ws_connect (conftest.py).
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

from tests.fakes.gateway import FakeWS, FakeWSRaiseAfter, make_ws, never
from tests.fakes.waiting import wait_until


def _closed_error(code: int | None = None, reason: str = "") -> ConnectionClosedError:
    rcvd = Close(code, reason) if code is not None else None
    return ConnectionClosedError(rcvd, None)


def _closed_ok(code: int = 1000, reason: str = "") -> ConnectionClosedOK:
    return ConnectionClosedOK(Close(code, reason), Close(code, reason), True)


@pytest.fixture(autouse=True)
def _sin_credenciales_cf(monkeypatch):
    """connect() lee CF_ACCESS_CLIENT_ID/SECRET del entorno real (issue #39):
    sin limpiarlas, el resultado dependería de la máquina o de otro test que
    las hubiera dejado puestas (p. ej. vía un .env cargado por load_config).
    Los tests que las necesitan las ponen con monkeypatch.setenv."""
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)


from config import GatewayConfig, AudioConfig, DeviceConfig, Config
from backends.gateway_client import (
    GatewayClient,
    GatewayDisconnected,
    GatewayHandshakeError,
    _cloudflare_access_headers,
)


async def _test_receive_separa_header_de_audio_binario() -> None:
    """Los frames binarios de jota-gateway llevan [0xA1][turn_seq uint16 BE]
    antes del PCM (protocolo documentado en jota-gateway/docs/client-protocol.md,
    commit 54a55d3). receive() debe separar la cabecera y devolver solo el
    PCM como 'audio' — si no, np.frombuffer revienta con tamaños impares
    (bug real visto en producción: buffer size must be a multiple of element size)."""
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    pcm = b"\x01\x02\x03\x04\x05\x06"
    frame = bytes([0xA1, 0x00, 0x01]) + pcm  # turn_seq=1
    client._ws = FakeWS([frame])

    events = [ev async for ev in client.receive()]

    assert len(events) == 1
    assert events[0].type == "tts_chunk"
    assert events[0].data["audio"] == pcm, (
        f"Esperaba PCM sin la cabecera de 3 bytes, got {events[0].data['audio']!r}"
    )
    assert events[0].data.get("turn_seq") == 1


async def _test_send_cancel_envia_mensaje() -> None:
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    ws_mock = make_ws()
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


def test_receive_separa_header_de_audio_binario() -> None:
    asyncio.run(_test_receive_separa_header_de_audio_binario())


def test_send_cancel_envia_mensaje() -> None:
    asyncio.run(_test_send_cancel_envia_mensaje())


def test_send_cancel_sin_ws_lanza_error() -> None:
    asyncio.run(_test_send_cancel_sin_ws_lanza_error())


def test_cloudflare_access_headers_vacio_por_defecto(monkeypatch) -> None:
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)
    assert _cloudflare_access_headers() == {}


def test_cloudflare_access_headers_con_ambas_variables(monkeypatch) -> None:
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "abc123")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "secret456")
    assert _cloudflare_access_headers() == {
        "CF-Access-Client-Id": "abc123",
        "CF-Access-Client-Secret": "secret456",
    }


def test_cloudflare_access_headers_con_solo_una_variable(monkeypatch) -> None:
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "abc123")
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)
    assert _cloudflare_access_headers() == {}


@pytest.mark.asyncio
async def test_connect_sin_credenciales_cf_no_manda_cabeceras(ws_connect) -> None:
    connect = ws_connect(AsyncMock(return_value=_ws_with_recv([_READY])))
    await GatewayClient(GatewayConfig(host="h", client_key="k", connect_timeout_s=1.0)).connect()
    assert connect.call_args.kwargs["additional_headers"] is None


@pytest.mark.asyncio
async def test_connect_con_credenciales_cf_las_manda_en_el_upgrade(monkeypatch, ws_connect) -> None:
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "abc123")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "secret456")
    connect = ws_connect(AsyncMock(return_value=_ws_with_recv([_READY])))
    await GatewayClient(GatewayConfig(host="h", client_key="k", connect_timeout_s=1.0)).connect()
    assert connect.call_args.kwargs["additional_headers"] == {
        "CF-Access-Client-Id": "abc123",
        "CF-Access-Client-Secret": "secret456",
    }


async def _test_receive_termina_en_turn_end_sin_esperar_done() -> None:
    """El gateway real (green-house) señaliza fin de turno con "turn_end", no
    con "done" — receive() debía quedarse esperando mensajes que nunca
    llegaban hasta el timeout de 30s de RESPONDING. Debe terminar también
    al ver turn_end, igual que hace con done."""
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    client._ws = FakeWS([
        json.dumps({"type": "pipeline_event", "stage": "tts_done"}),
        json.dumps({"type": "turn_end", "turn_id": "t-1"}),
        json.dumps({"type": "pipeline_event", "stage": "nunca_deberia_llegar"}),
    ])

    events = [ev async for ev in client.receive()]

    # Revisión Fase R: receive() ya no termina en turn_end; lo entrega como
    # evento (con su turn_id) y sigue leyendo — quien decide si es el fin de
    # SU turno es _responding, y el audio tardío tras turn_end no se pierde.
    assert [e.type for e in events] == ["pipeline_event", "turn_end", "pipeline_event"]
    assert events[1].data["turn_id"] == "t-1"


def test_receive_termina_en_turn_end_sin_esperar_done() -> None:
    asyncio.run(_test_receive_termina_en_turn_end_sin_esperar_done())


# ---------------------------------------------------------------------------
# Issue #15: connect() debe esperar el mensaje 'ready' antes de retornar.
#
# Sin esto, si el gateway rechaza el handshake (p.ej. client_key inválida),
# el cliente envía todo el turno de audio a una conexión muerta, descubriendo
# el error tarde y de forma genérica al final del turno.
# ---------------------------------------------------------------------------


async def _test_connect_espera_mensaje_ready_antes_de_retornar(ws_connect) -> None:
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws_mock = make_ws()
    ws_mock.recv.return_value = json.dumps({"type": "ready"})
    ws_connect(AsyncMock(return_value=ws_mock))

    client = GatewayClient(cfg)
    await client.connect()

    ws_mock.recv.assert_awaited_once()
    ws_mock.send.assert_awaited_once()


async def _test_connect_levanta_si_primer_mensaje_es_error(ws_connect) -> None:
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws_mock = make_ws()
    ws_mock.recv.return_value = json.dumps({"type": "error", "message": "bad key"})
    ws_connect(AsyncMock(return_value=ws_mock))

    client = GatewayClient(cfg)
    try:
        await client.connect()
    except RuntimeError as exc:
        assert "bad key" in str(exc), f"Mensaje del error no propagado: {exc}"
    else:
        raise AssertionError("connect() debería haber lanzado RuntimeError por mensaje 'error'")


async def _test_connect_levanta_si_primer_mensaje_tipo_inesperado(ws_connect) -> None:
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws_mock = make_ws()
    ws_mock.recv.return_value = json.dumps({"type": "transcription"})
    ws_connect(AsyncMock(return_value=ws_mock))

    client = GatewayClient(cfg)
    try:
        await client.connect()
    except RuntimeError as exc:
        assert "transcription" in str(exc), f"Tipo inesperado no propagado: {exc}"
    else:
        raise AssertionError("connect() debería haber lanzado RuntimeError por tipo inesperado")


async def _test_connect_propagar_connection_closed_si_server_cierra_antes_de_ready(ws_connect) -> None:
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws_mock = make_ws()
    ws_mock.recv.side_effect = _closed_error(1008, "Clave de cliente invalida o inactiva")
    ws_connect(AsyncMock(return_value=ws_mock))

    client = GatewayClient(cfg)
    try:
        await client.connect()
    except GatewayHandshakeError as exc:
        # Issue #107: el cierre 1008 del handshake se traduce a un error
        # tipado con la causa, no se propaga la excepción de la librería.
        assert exc.code == 1008
        assert exc.kind == "invalid_client_key"
        assert exc.fatal is True
    else:
        raise AssertionError("connect() debería lanzar GatewayHandshakeError si el server cierra")
    assert client.is_connected is False
    ws_mock.close.assert_awaited()


async def _test_connect_levanta_timeout_si_server_no_responde_ready(ws_connect) -> None:
    """El recv() del handshake respeta cfg.connect_timeout_s: si el gateway
    no envía ready a tiempo, asyncio.TimeoutError se loguea y se re-lanza.
    Pinea la rama del except (sin capturarla genéricamente como
    ConnectionClosed) para que un cambio accidental futuro no la trague."""
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=0.05)

    async def _slow_recv() -> str:
        await asyncio.sleep(1.0)
        return json.dumps({"type": "ready"})

    ws_mock = make_ws()
    ws_mock.recv.side_effect = _slow_recv
    ws_connect(AsyncMock(return_value=ws_mock))

    client = GatewayClient(cfg)
    try:
        await client.connect()
    except asyncio.TimeoutError:
        pass
    else:
        raise AssertionError("connect() debería haber levantado TimeoutError esperando ready")


async def _test_connect_propagar_json_invalido_en_respuesta_handshake(ws_connect) -> None:
    """Si el primer mensaje no es JSON (p.ej. proxy/LB devolviendo HTML 502
    antes del WS), connect() propaga json.JSONDecodeError sin capturarla.
    Es la política 'fail fast' del handshake, deliberadamente distinta del
    'tolerant skip' de receive() — un cambio accidental aquí debe romper este
    test, no degradar silenciosamente."""
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws_mock = make_ws()
    ws_mock.recv.return_value = "<html>502 Bad Gateway</html>"
    ws_connect(AsyncMock(return_value=ws_mock))

    client = GatewayClient(cfg)
    try:
        await client.connect()
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("connect() debería propagar JSONDecodeError del handshake")


def test_connect_espera_mensaje_ready_antes_de_retornar(ws_connect) -> None:
    asyncio.run(_test_connect_espera_mensaje_ready_antes_de_retornar(ws_connect))


def test_connect_levanta_si_primer_mensaje_es_error(ws_connect) -> None:
    asyncio.run(_test_connect_levanta_si_primer_mensaje_es_error(ws_connect))


def test_connect_levanta_si_primer_mensaje_tipo_inesperado(ws_connect) -> None:
    asyncio.run(_test_connect_levanta_si_primer_mensaje_tipo_inesperado(ws_connect))


def test_connect_propagar_connection_closed_si_server_cierra_antes_de_ready(ws_connect) -> None:
    asyncio.run(_test_connect_propagar_connection_closed_si_server_cierra_antes_de_ready(ws_connect))


def test_connect_levanta_timeout_si_server_no_responde_ready(ws_connect) -> None:
    asyncio.run(_test_connect_levanta_timeout_si_server_no_responde_ready(ws_connect))


def test_connect_propagar_json_invalido_en_respuesta_handshake(ws_connect) -> None:
    asyncio.run(_test_connect_propagar_json_invalido_en_respuesta_handshake(ws_connect))


# ---------------------------------------------------------------------------
# Issue #24: GatewayClient.__init__ acepta bus opcional y atributos de sesión.
# ---------------------------------------------------------------------------


def test_gateway_client_init_sin_bus_no_falla() -> None:
    """Issue #24: bus sigue siendo opcional (legacy preservado)."""
    from config import GatewayConfig
    cfg = GatewayConfig(host="127.0.0.1", client_key="x")
    client = GatewayClient(cfg)
    assert client.is_connected is False
    assert client._session_id is None
    assert client._agent is None
    assert client._capabilities == {}
    assert client._current_turn_seq == 0


def test_gateway_client_init_con_bus() -> None:
    """Issue #24: bus inyectado se guarda para publicar gateway_ready."""
    from config import GatewayConfig
    from domain.event_bus import EventBus
    cfg = GatewayConfig(host="127.0.0.1", client_key="x")
    bus = EventBus()
    client = GatewayClient(cfg, bus=bus)
    assert client._bus is bus


def test_gateway_client_ya_no_acepta_device_id() -> None:
    """Issue #83: device_id no forma parte del handshake documentado
    (client-protocol.md §1); tras estar deprecado e ignorado (#24) se retira."""
    from config import GatewayConfig
    cfg = GatewayConfig(host="127.0.0.1", client_key="x")
    with pytest.raises(TypeError):
        GatewayClient(cfg, device_id="legacy-1")  # type: ignore[call-arg]


def test_gateway_client_sin_device_id_sin_warning() -> None:
    """Issue #24: instanciar sin device_id no debe emitir DeprecationWarning."""
    from config import GatewayConfig
    cfg = GatewayConfig(host="127.0.0.1", client_key="x")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        GatewayClient(cfg)
    deprecation = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert not deprecation


# ---------------------------------------------------------------------------
# Issue #16: receive() debe distinguir cierre limpio (ConnectionClosedOK,
# código 1000/1001) de cierre anómalo (ConnectionClosedError, 1006/1009/...).
# El actual `except ConnectionClosed` traga ambos casos, así que un cierre
# anómalo a mitad de turno es invisible para state_machine — el bug que
# cierra la issue.
# ---------------------------------------------------------------------------


async def _test_receive_propagates_connection_closed_error() -> None:
    """Si el servidor cae a mitad de turno (ej. 1006 abnormal closure, 1009
    mensaje >max_size, 1011 internal error), websockets lanza
    ConnectionClosedError — receive() debe propagarlo para que state_machine
    lo vea en receive_task.exception() y publique VoiceEvent(type='error').

    Antes del fix, el `except ConnectionClosed` genérico de receive() lo
    tragaba (junto con el OK), y el turno terminaba con playback_ended como
    si todo hubiera ido bien."""
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    pcm = b"\x00\x01" * 100
    frame = bytes([0xA1, 0x00, 0x01]) + pcm  # turn_seq=1 + PCM
    client._ws = FakeWSRaiseAfter([frame], _closed_error())  # 1006, sin close frame

    try:
        events = [ev async for ev in client.receive()]
    except GatewayDisconnected as exc:
        # Lo que esperamos: que el cierre se propague, no que se trague.
        assert exc.code == 1006
        assert client.is_connected is False
    else:
        raise AssertionError(
            "receive() debería propagar ConnectionClosedError (cierre anómalo "
            "del servidor), pero terminó silenciosamente como si fuera un "
            f"fin de turno normal. Eventos recibidos: {events!r}"
        )


async def _test_receive_propagates_clean_close_as_disconnected() -> None:
    """Issue #118 (sustituye al antiguo "OK termina en silencio"): el gateway
    nunca cierra la sesión al acabar un turno (para eso está turn_end), así
    que un cierre limpio 1000/1001 mientras se lee es inactividad, reinicio
    o despliegue. Debe propagarse como GatewayDisconnected y dejar
    is_connected=False; tragarlo dejaba un socket muerto "conectado" y el
    turno en curso acababa como si hubiera ido bien."""
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    pcm = b"\x00\x01" * 100
    frame = bytes([0xA1, 0x00, 0x01]) + pcm
    client._ws = FakeWSRaiseAfter([frame], _closed_ok(1001, "going away"))

    events = []
    with pytest.raises(GatewayDisconnected) as info:
        async for ev in client.receive():
            events.append(ev)
    assert len(events) == 1 and events[0].type == "tts_chunk"
    assert info.value.code == 1001
    assert info.value.fatal is False
    assert client.is_connected is False
    assert client.last_close is info.value


def test_receive_propagates_connection_closed_error() -> None:
    asyncio.run(_test_receive_propagates_connection_closed_error())


def test_receive_propagates_clean_close_as_disconnected() -> None:
    asyncio.run(_test_receive_propagates_clean_close_as_disconnected())


# ---------------------------------------------------------------------------
# Issue #26: open_timeout de websockets debe heredar connect_timeout_s.
# ---------------------------------------------------------------------------


def _cfg(connect_timeout_s: float = 30.0) -> Config:
    return Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="x", connect_timeout_s=connect_timeout_s),
        device=DeviceConfig(id="test"),
        audio=AudioConfig(),
    )


@pytest.mark.asyncio
async def test_connect_passes_open_timeout_to_websockets(ws_connect) -> None:
    """websockets.connect() debe recibir open_timeout=cfg.connect_timeout_s;
    de lo contrario, websockets usa open_timeout=10 (default) y un valor
    superior en connect_timeout_s es un no-op silencioso.

    Antes parcheaba el módulo websockets entero con un MagicMock y se
    tragaba cualquier excepción de connect(): el `except
    websockets.exceptions.ConnectionClosed` del handshake se evaluaba contra
    un MagicMock y lanzaba TypeError, que el test ocultaba. Ahora solo se
    sustituye connect() y el handshake tiene que completarse."""
    cfg = _cfg(connect_timeout_s=30.0)
    client = GatewayClient(cfg.gateway)
    connect = ws_connect(AsyncMock(return_value=_ws_with_recv([_READY])))

    await client.connect()

    assert client.is_connected
    connect.assert_awaited_once()
    call_kwargs = connect.call_args.kwargs
    assert "open_timeout" in call_kwargs, (
        "open_timeout no se ha pasado a websockets.connect() — "
        "issue #26 sigue vigente"
    )
    assert call_kwargs["open_timeout"] == 30.0, (
        f"open_timeout esperaba 30.0 (de connect_timeout_s), "
        f"obtuve {call_kwargs['open_timeout']}"
    )


# ---------------------------------------------------------------------------
# Issue #24: connect() parsea ready completo y publica gateway_ready al bus.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_parsea_session_id_y_agent_de_ready(ws_connect):
    """Issue #24: connect() extrae session_id, agent y capabilities de ready."""
    from config import GatewayConfig
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws_mock = make_ws()
    ws_mock.recv.return_value = json.dumps({
        "type": "ready",
        "session_id": "hab_sito:1751289600000",
        "agent": "main",
        "input_mode": "audio",
        "output_mode": ["audio", "text", "status"],
        "requested_capabilities": {"barge_in": True, "tts": True, "transcriber": True},
        "live_capabilities": {"barge_in": True, "tts": False, "transcriber": True},
    })
    ws_connect(AsyncMock(return_value=ws_mock))

    client = GatewayClient(cfg)
    await client.connect()

    assert client._session_id == "hab_sito:1751289600000"
    assert client._agent == "main"
    assert client._capabilities["requested"]["barge_in"] is True
    assert client._capabilities["live"]["tts"] is False


@pytest.mark.asyncio
async def test_connect_publica_gateway_ready_al_bus(ws_connect):
    """Issue #24: connect() publica gateway_ready al bus con session_id."""
    from config import GatewayConfig
    from domain.event_bus import EventBus
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws_mock = make_ws()
    ws_mock.recv.return_value = json.dumps({
        "type": "ready", "session_id": "s-1", "agent": "main",
        "requested_capabilities": {}, "live_capabilities": {},
    })
    ws_connect(AsyncMock(return_value=ws_mock))

    bus = EventBus()
    captured = []

    async def _capture_one():
        async for ev in bus.subscribe():
            captured.append(ev)
            break

    t = asyncio.create_task(_capture_one())
    await asyncio.sleep(0)  # deja que el suscriptor se registre
    client = GatewayClient(cfg, bus=bus)
    await client.connect()
    await asyncio.wait_for(t, timeout=1.0)

    gateway_ready_evts = [e for e in captured if e.type == "gateway_ready"]
    assert len(gateway_ready_evts) == 1
    assert gateway_ready_evts[0].data["session_id"] == "s-1"


@pytest.mark.asyncio
async def test_connect_no_publica_sin_bus(ws_connect):
    """Issue #24: sin bus inyectado, connect() no intenta publicar."""
    from config import GatewayConfig
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws_mock = make_ws()
    ws_mock.recv.return_value = json.dumps({
        "type": "ready", "session_id": "s-1", "agent": "main",
        "requested_capabilities": {}, "live_capabilities": {},
    })
    ws_connect(AsyncMock(return_value=ws_mock))

    client = GatewayClient(cfg)  # bus=None
    await client.connect()  # no debe intentar publicar (no fallaría sin bus porque la rama está guardada)


def test_connect_handshake_sin_device_id(ws_connect):
    """Issue #24: handshake NO incluye device_id (el gateway lo ignora)."""
    from config import GatewayConfig
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws_mock = make_ws()
    ws_mock.recv.return_value = json.dumps({
        "type": "ready", "session_id": "s-1", "agent": "main",
        "requested_capabilities": {}, "live_capabilities": {},
    })
    captured_send = []
    ws_mock.send.side_effect = lambda x: captured_send.append(json.loads(x))
    ws_connect(AsyncMock(return_value=ws_mock))

    asyncio.run(GatewayClient(cfg).connect())

    assert len(captured_send) == 1
    handshake = captured_send[0]
    assert "device_id" not in handshake, "device_id no debe viajar en el handshake"
    assert handshake["client_key"] == "test"
    assert handshake["input_mode"] == "audio"
    assert "audio" in handshake["output_mode"]


# ---------------------------------------------------------------------------
# Issue #24: receive() debe rendir turn_start, interrupted, tool_call y
# gestionar _current_turn_seq para descartar tts_chunk de turnos cancelados.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_receive_rinde_turn_start():
    """Issue #24: receive() rinde turn_start como GatewayEvent."""
    from config import GatewayConfig
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    client._ws = FakeWS([json.dumps({
        "type": "turn_start", "turn_id": "t-1", "turn_seq": 42,
    })])

    events = [ev async for ev in client.receive()]

    assert len(events) == 1
    assert events[0].type == "turn_start"
    assert events[0].data["turn_id"] == "t-1"
    assert events[0].data["turn_seq"] == 42


@pytest.mark.asyncio
async def test_receive_rinde_interrupted():
    """Issue #24: receive() rinde interrupted (clave para barge-in real)."""
    from config import GatewayConfig
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    client._ws = FakeWS([json.dumps({"type": "interrupted"})])

    events = [ev async for ev in client.receive()]

    assert len(events) == 1
    assert events[0].type == "interrupted"


@pytest.mark.asyncio
async def test_receive_rinde_tool_call_start_y_result():
    """Issue #24: receive() rinde tool_call start y result (opt-in)."""
    from config import GatewayConfig
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    client._ws = FakeWS([
        json.dumps({
            "type": "tool_call", "turn_id": "t-1", "phase": "start",
            "name": "exec", "tool_call_id": "call-1",
            "args": {"command": "ls"}, "result": None, "is_error": None,
        }),
        json.dumps({
            "type": "tool_call", "turn_id": "t-1", "phase": "result",
            "name": "exec", "tool_call_id": "call-1",
            "args": None, "result": "file.txt", "is_error": False,
        }),
    ])

    events = [ev async for ev in client.receive()]

    assert len(events) == 2
    assert events[0].type == "tool_call"
    assert events[0].data["phase"] == "start"
    assert events[1].data["phase"] == "result"
    assert events[1].data["result"] == "file.txt"


@pytest.mark.asyncio
async def test_current_turn_seq_se_actualiza_con_turn_start_mayor():
    """Issue #24: turn_start con seq mayor incrementa _current_turn_seq."""
    from config import GatewayConfig
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    assert client._current_turn_seq == 0
    client._ws = FakeWS([json.dumps({
        "type": "turn_start", "turn_id": "t-1", "turn_seq": 5,
    })])

    _ = [ev async for ev in client.receive()]

    assert client._current_turn_seq == 5


@pytest.mark.asyncio
async def test_current_turn_seq_ignora_turn_start_menor_o_igual(caplog):
    """Issue #24: turn_start con seq <= current se rinde pero NO incrementa (defensa)."""
    from config import GatewayConfig
    import logging
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    client._current_turn_seq = 10
    client._ws = FakeWS([json.dumps({
        "type": "turn_start", "turn_id": "t-2", "turn_seq": 10,
    })])

    with caplog.at_level(logging.WARNING, logger="backends.gateway_client"):
        events = [ev async for ev in client.receive()]

    assert client._current_turn_seq == 10, "No debe decrementar ni quedarse igual"
    assert len(events) == 1  # se rinde igual para que el caller lo vea
    assert any("turn_start" in r.message and "current=10" in r.message for r in caplog.records), \
        "Debe emitir WARNING con old/current"


@pytest.mark.asyncio
async def test_tts_chunk_se_descarta_si_turn_seq_menor():
    """Issue #24: tts_chunk con turn_seq < _current_turn_seq se descarta (no se rinde)."""
    from config import GatewayConfig
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    client._current_turn_seq = 10
    pcm = b"\x00\x01" * 50
    frame_old = bytes([0xA1, 0x00, 0x05]) + pcm  # turn_seq=5 (anterior)
    frame_new = bytes([0xA1, 0x00, 0x0A]) + pcm  # turn_seq=10 (igual)
    client._ws = FakeWS([frame_old, frame_new])

    events = [ev async for ev in client.receive()]

    # Solo el frame con seq=10 llega (>= current, monotónico);
    # el de seq=5 se descarta.
    assert len(events) == 1
    assert events[0].type == "tts_chunk"
    assert events[0].data["turn_seq"] == 10


# ---------------------------------------------------------------------------
# Fase R (#110, #107, #118): handshake robusto y estado de sesión coherente.
# ---------------------------------------------------------------------------


def _ws_with_recv(messages):
    """Socket abierto cuyo recv() entrega `messages` en orden."""
    return make_ws(recv=messages)


_READY = json.dumps({
    "type": "ready", "session_id": "s-2", "agent": "main",
    "requested_capabilities": {"barge_in": True, "tts": True, "transcriber": True},
    "live_capabilities": {"barge_in": False, "tts": True, "transcriber": False},
})


@pytest.mark.asyncio
async def test_connect_acepta_status_antes_de_ready_y_lo_publica(ws_connect):
    """client-protocol.md §1/§5: pueden llegar `status` antes de `ready`
    (health check con transcriber caído). Antes connect() lo rechazaba como
    "tipo inesperado" y con un servicio degradado no se podía reconectar."""
    from domain.event_bus import EventBus
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    status = json.dumps({"type": "status", "service": "transcriber", "state": "unavailable"})
    ws = _ws_with_recv([status, _READY])
    ws_connect(AsyncMock(return_value=ws))
    bus = EventBus()
    seen = []

    async def _sub():
        async for ev in bus.subscribe():
            seen.append(ev)
            if ev.type == "gateway_ready":
                return

    sub = asyncio.create_task(_sub())
    await asyncio.sleep(0)
    client = GatewayClient(cfg, bus=bus)
    await client.connect()
    await asyncio.wait_for(sub, 1.0)

    assert [e.type for e in seen] == ["gateway_status", "gateway_ready"]
    assert seen[0].data["service"] == "transcriber"
    # #110: requested y live separados, como el protocolo v1.15.0+.
    assert seen[1].data["capabilities"]["live"]["transcriber"] is False
    assert seen[1].data["capabilities"]["requested"]["transcriber"] is True
    assert client.is_connected


@pytest.mark.asyncio
async def test_connect_reinicia_turn_seq_y_sesion(ws_connect):
    """#118: el gateway reinicia turn_seq en cada sesión; con el valor viejo
    se descartarían todos los frames de la sesión nueva."""
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    client = GatewayClient(cfg)
    client._current_turn_seq = 7
    client._session_id = "vieja"
    ws_connect(AsyncMock(return_value=_ws_with_recv([_READY])))
    await client.connect()
    assert client._current_turn_seq == 0
    assert client._session_id == "s-2"


@pytest.mark.asyncio
async def test_connect_pasa_keepalive_y_cola_explicitos(ws_connect):
    """#118/#116: ping explícito (configurable) y max_queue=None para que la
    librería vacíe el socket aunque la app lea al ritmo de reproducción."""
    cfg = GatewayConfig(
        host="127.0.0.1", client_key="test", connect_timeout_s=1.0,
        ping_interval_s=15.0, ping_timeout_s=25.0,
    )
    connect = AsyncMock(return_value=_ws_with_recv([_READY]))
    ws_connect(connect)
    await GatewayClient(cfg).connect()
    kw = connect.call_args.kwargs
    assert kw["ping_interval"] == 15.0
    assert kw["ping_timeout"] == 25.0
    assert kw["max_queue"] is None
    # Issue #113: tope explícito en vez de None (sin límite), holgado para
    # los frames TTS grandes que el default de 1 MiB cortaba con 1009.
    assert kw["max_size"] == 16 * 1024 * 1024


@pytest.mark.asyncio
async def test_connect_ping_cero_lo_desactiva(ws_connect):
    cfg = GatewayConfig(host="127.0.0.1", client_key="t", ping_interval_s=0)
    connect = AsyncMock(return_value=_ws_with_recv([_READY]))
    ws_connect(connect)
    await GatewayClient(cfg).connect()
    assert connect.call_args.kwargs["ping_interval"] is None


@pytest.mark.parametrize("code,reason,kind,fatal", [
    (1008, "Clave de cliente invalida o inactiva", "invalid_client_key", True),
    (1008, "Agent 'casa' not permitted for this client.", "agent_not_permitted", True),
    (1008, "Agent 'casa' not available.", "agent_not_available", False),
    (1008, "Handshake timeout", "handshake_timeout", False),
    (1008, "Handshake invalido", "handshake_invalid", True),
    (1011, "Servicio critico no disponible", "service_unavailable", False),
    (1011, "Servicio de identidad no disponible", "service_unavailable", False),
])
def test_handshake_1008_distingue_causa(code, reason, kind, fatal, ws_connect):
    """#107: la causa del rechazo solo viaja en el reason del close frame
    (client-protocol.md §1 y nota del §9: no hay mensaje `error` previo)."""
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws = _ws_with_recv([_closed_error(code, reason)])
    ws_connect(AsyncMock(return_value=ws))
    client = GatewayClient(cfg)

    with pytest.raises(GatewayHandshakeError) as info:
        asyncio.run(client.connect())
    assert info.value.kind == kind
    assert info.value.code == code
    assert info.value.fatal is fatal
    assert (client.fatal_error is info.value) is fatal
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_connect_http_403_es_fatal(ws_connect):
    """Cloudflare Access sin credenciales válidas → HTTP 403 en el upgrade."""
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response
    resp = create_autospec(Response, instance=True)
    resp.status_code = 403
    ws_connect(AsyncMock(side_effect=InvalidStatus(resp)))
    with pytest.raises(GatewayHandshakeError) as info:
        await GatewayClient(GatewayConfig(host="h", client_key="k")).connect()
    assert info.value.kind == "http_rejected" and info.value.fatal


@pytest.mark.asyncio
async def test_ensure_connected_comparte_un_unico_intento(ws_connect):
    """Supervisor de IDLE e inicio de turno pueden pedir conexión a la vez."""
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    gate = asyncio.Event()

    async def _slow_connect(*a, **kw):
        await gate.wait()
        return _ws_with_recv([_READY])

    connect = AsyncMock(side_effect=_slow_connect)
    ws_connect(connect)
    client = GatewayClient(cfg)
    t1 = asyncio.create_task(client.ensure_connected())
    t2 = asyncio.create_task(client.ensure_connected())
    # Ambas peticiones en marcha y el intento bloqueado en el gate (antes:
    # sleep(0.01), issue #76).
    await wait_until(lambda: connect.call_count >= 1)
    gate.set()
    await asyncio.gather(t1, t2)
    assert connect.await_count == 1
    assert client.is_connected


@pytest.mark.asyncio
async def test_connect_cancelado_no_deja_socket_medio_abierto(ws_connect):
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=5.0)
    ws = make_ws()
    ws.recv.side_effect = never
    ws_connect(AsyncMock(return_value=ws))
    client = GatewayClient(cfg)
    t = asyncio.create_task(client.connect())
    await wait_until(lambda: ws.recv.await_count >= 1)  # en pleno handshake
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert client.is_connected is False
    ws.close.assert_awaited()


@pytest.mark.asyncio
async def test_send_tras_cierre_lanza_gateway_disconnected():
    client = GatewayClient(GatewayConfig(host="h", client_key="k"))
    ws = make_ws()
    ws.send.side_effect = _closed_error(1013, "client too slow")
    client._ws = ws
    with pytest.raises(GatewayDisconnected) as info:
        await client.send_audio(b"x")
    assert info.value.code == 1013 and not info.value.fatal
    assert client.is_connected is False
    # Los envíos siguientes fallan igual, sin tocar el socket muerto.
    with pytest.raises(GatewayDisconnected):
        await client.send_end()
    assert ws.send.await_count == 1


@pytest.mark.asyncio
async def test_send_audio_envia_float32_crudo_sin_cabecera():
    """Issue #84: la subida NO lleva cabecera ni turn_seq, a diferencia de la
    bajada TTS ([0xA1][turn_seq][PCM16]). Lo fija el protocolo del gateway
    (client-protocol.md §3 y §12: "Header: ninguno"); una cabecera aquí se
    interpretaría como muestras de audio."""
    import numpy as np

    client = GatewayClient(GatewayConfig(host="h", client_key="k"))
    ws = make_ws()
    client._ws = ws
    frame = np.array([0.0, 0.5, -0.5, 1.0], dtype="<f4").tobytes()
    await client.send_audio(frame)
    ws.send.assert_awaited_once_with(frame)


@pytest.mark.asyncio
async def test_send_keepalive_es_json_ignorado_por_el_gateway():
    client = GatewayClient(GatewayConfig(host="h", client_key="k"))
    ws = make_ws()
    client._ws = ws
    await client.send_keepalive()
    assert json.loads(ws.send.call_args[0][0]) == {"type": "keepalive"}
    assert client.seconds_since_last_send < 1.0


def test_receive_sin_conexion_lanza_gateway_disconnected():
    client = GatewayClient(GatewayConfig(host="h", client_key="k"))

    async def _t():
        with pytest.raises(GatewayDisconnected):
            async for _ in client.receive():
                pass

    asyncio.run(_t())


@pytest.mark.asyncio
async def test_receive_token_expone_el_texto_como_content():
    """#2: el gateway manda {"type":"token","text":...} (client-protocol §5),
    pero _receive_loop lee data["content"] (formato antiguo): los tokens
    llegaban vacíos a playback y al bus."""
    client = GatewayClient(GatewayConfig(host="h", client_key="k"))
    client._ws = FakeWS([json.dumps({"type": "token", "turn_id": "t-1", "text": "Hola"})])
    events = [ev async for ev in client.receive()]
    assert events[0].type == "llm_token"
    assert events[0].data["content"] == "Hola"
    assert events[0].data["text"] == "Hola"


@pytest.mark.asyncio
async def test_cierre_1008_a_mitad_de_sesion_marca_fatal_y_connect_lo_limpia(ws_connect):
    client = GatewayClient(GatewayConfig(host="h", client_key="k", connect_timeout_s=1.0))
    ws = make_ws()
    ws.send.side_effect = _closed_error(1008, "Agent 'x' not permitted for this client.")
    client._ws = ws
    with pytest.raises(GatewayDisconnected):
        await client.send_end()
    assert client.fatal_error is not None and client.fatal_error.kind == "agent_not_permitted"
    ws_connect(AsyncMock(side_effect=OSError("red caída")))
    with pytest.raises(OSError):
        await client.connect()
    assert client.fatal_error is None, "el último intento (transitorio) manda"



@pytest.mark.asyncio
async def test_tras_cancel_se_descarta_el_audio_del_turno_cancelado():
    """Revisión Fase R, punto 3: tras cancelar el turno N, sus frames en
    tránsito tienen seq == current y se colaban al principio de N+1."""
    client = GatewayClient(GatewayConfig(host="h", client_key="k"))
    ws = make_ws()
    client._ws = ws
    client._current_turn_seq = 3
    await client.send_cancel()
    pcm = b"\x00\x01"
    client._ws = FakeWS([
        bytes([0xA1, 0x00, 0x03]) + pcm,   # turno 3 cancelado, en tránsito
        json.dumps({"type": "turn_start", "turn_id": "t-4", "turn_seq": 4}),
        bytes([0xA1, 0x00, 0x04]) + pcm,
    ])
    events = [ev async for ev in client.receive()]
    assert [e.type for e in events] == ["turn_start", "tts_chunk"]
    assert events[1].data["turn_seq"] == 4


@pytest.mark.asyncio
async def test_connect_reinicia_el_descarte_tras_cancel(ws_connect):
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    client = GatewayClient(cfg)
    client._discard_upto = 9
    ws_connect(AsyncMock(return_value=_ws_with_recv([_READY])))
    await client.connect()
    assert client._discard_upto == 0



@pytest.mark.asyncio
async def test_connect_un_unico_deadline_para_abrir_y_handshake(ws_connect, monkeypatch):
    """Revisión Fase R, punto 4: abrir el socket y esperar `ready` comparten
    connect_timeout_s (antes eran dos plazos completos seguidos).

    Antes se medía el tiempo total contra un umbral (< 0.45 s con un plazo
    de 0.3 s): con la máquina cargada fallaba sin bug (issue #76). Ahora se
    mira el plazo que connect() le da al recv() del handshake: tras gastar
    al menos 0.05 s abriendo, debe quedar menos que el plazo completo. La
    lentitud del entorno solo puede reducirlo, y el plazo (5 s) queda lejos
    de la apertura, así que tampoco puede vencer antes de tiempo."""
    budget, open_delay = 5.0, 0.05
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=budget)
    timeouts: list[float] = []
    real_wait_for = asyncio.wait_for

    async def _recording_wait_for(aw, timeout):
        timeouts.append(timeout)
        return await real_wait_for(aw, timeout)

    async def _slow_open(*a, **kw):
        await asyncio.sleep(open_delay)
        # recv falla al instante (JSON inválido): el test no espera el plazo.
        return make_ws(recv=["<html>502</html>"])

    ws_connect(_slow_open)
    with monkeypatch.context() as m:
        m.setattr(asyncio, "wait_for", _recording_wait_for)
        with pytest.raises(json.JSONDecodeError):
            await GatewayClient(cfg).connect()

    assert len(timeouts) >= 2, f"esperaba wait_for de apertura y de recv: {timeouts}"
    open_timeout, recv_timeout = timeouts[0], timeouts[1]
    assert open_timeout == pytest.approx(budget)
    assert recv_timeout <= budget - open_delay + 1e-6, (
        f"al recv del handshake le quedaban {recv_timeout:.3f}s de {budget}s: abrir + "
        "handshake deben caber en un único connect_timeout_s"
    )


@pytest.mark.asyncio
async def test_cancelar_handshake_no_espera_el_close_lento(ws_connect):
    """Revisión Fase R, punto 5: cancelar un handshake hacía await ws.close()
    con el close_timeout por defecto (10 s) y retrasaba RECORDING."""
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=5.0)
    ws = make_ws()
    aborted = []
    ws.transport.abort.side_effect = lambda: aborted.append(True)

    async def _slow_close():
        await asyncio.sleep(10)

    ws.close.side_effect = _slow_close
    ws.recv.side_effect = never
    ws_connect(AsyncMock(return_value=ws))
    client = GatewayClient(cfg)
    t = asyncio.create_task(client.connect())
    await wait_until(lambda: ws.recv.await_count >= 1)  # antes: sleep(0.02)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    t.cancel()
    await asyncio.wait([t], timeout=9)
    assert t.cancelled()
    # El bug esperaba el close lento entero (10 s); el fix aborta a los
    # _ABORT_CLOSE_S (0.5 s). Margen amplio para no fallar por carga (#76).
    assert loop.time() - t0 < 5.0
    assert aborted, "debe abortar el transporte si el close no termina enseguida"


def test_handshake_error_conserva_el_code_del_gateway(ws_connect) -> None:
    """Issue #113: un `error` antes de `ready` ya no pierde su `code`; va en
    el reason (el atributo .code sigue siendo el close code WS, aquí None)."""
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws_mock = make_ws()
    ws_mock.recv.return_value = json.dumps(
        {"type": "error", "code": "AUTH_FAILED", "message": "bad key", "fatal": True}
    )
    ws_connect(AsyncMock(return_value=ws_mock))

    with pytest.raises(GatewayHandshakeError) as info:
        asyncio.run(GatewayClient(cfg).connect())
    assert info.value.code is None
    assert info.value.reason == "AUTH_FAILED: bad key"
    assert info.value.fatal is True


def test_cierre_1009_es_recuperable_y_tiene_etiqueta():
    """Issue #113: un frame que supere max_size cierra con 1009; se trata
    como cualquier corte recuperable y el log dice qué pasó."""
    from backends.gateway_client import close_code_label, close_is_fatal

    assert close_is_fatal(1009) is False
    assert "demasiado grande" in close_code_label(1009)
