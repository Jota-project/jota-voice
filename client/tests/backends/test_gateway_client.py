"""Tests de GatewayClient — no requieren servidor real."""
from __future__ import annotations

import asyncio
import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock

# --- Stubs ---
# Sobreescribimos ConnectionClosed/OK/Error con fakes laxos que aceptan
# cualquier firma (el real exige rcvd, sent). Necesario porque pytest puede
# cargar el módulo real (websockets 16.0+) vía otros tests antes que este
# archivo. NO reemplazamos el módulo entero: sería no-paquete y rompería las
# lazy imports que el módulo real hace para `websockets.connect`, etc.
def _ensure_exception_subclasses() -> None:
    try:
        from websockets import exceptions as _exc
    except ImportError:
        # Si websockets no está disponible, tampoco se ejecuta el código de
        # producción que lo usa — los tests no pueden correr de todas formas.
        return
    _base = getattr(_exc, "ConnectionClosed", Exception)

    class _FakeConnectionClosed(Exception):
        """Base laxa — hereda Exception, no la versión real con init estricto
        (rcvd, sent). Tests solo necesitan isinstance para distinguir OK vs Error."""

    class _FakeConnectionClosedOK(_FakeConnectionClosed):
        pass

    class _FakeConnectionClosedError(_FakeConnectionClosed):
        pass

    # Sustituimos ConnectionClosed base también: si no, isinstance contra el
    # real no encontraría las subclases fake (MRO separado).
    _exc.ConnectionClosed = _FakeConnectionClosed
    _exc.ConnectionClosedOK = _FakeConnectionClosedOK
    _exc.ConnectionClosedError = _FakeConnectionClosedError

_ensure_exception_subclasses()

from config import GatewayConfig
from backends.gateway_client import GatewayClient, _cloudflare_access_headers


class _FakeConnectionClosed(Exception):
    """Stub para ConnectionClosed: el real (websockets.exceptions) requiere
    frames rcvd/sent en el constructor, demasiado para este test. connect()
    solo necesita propagar la excepción sin capturarla, así que cualquier
    subclase de Exception sirve."""


class _FakeWS:
    """WS fake iterable async — basta para probar receive() sin servidor real."""

    def __init__(self, messages: list) -> None:
        self._messages = messages

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for m in self._messages:
            yield m


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
    client._ws = _FakeWS([frame])

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


class _FakeWS:
    """Async-iterable mínimo que simula los mensajes de un websocket real."""

    def __init__(self, messages: list) -> None:
        self._messages = list(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


async def _test_receive_termina_en_turn_end_sin_esperar_done() -> None:
    """El gateway real (green-house) señaliza fin de turno con "turn_end", no
    con "done" — receive() debía quedarse esperando mensajes que nunca
    llegaban hasta el timeout de 30s de RESPONDING. Debe terminar también
    al ver turn_end, igual que hace con done."""
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    client._ws = _FakeWS([
        json.dumps({"type": "pipeline_event", "stage": "tts_done"}),
        json.dumps({"type": "turn_end", "turn_id": "t-1"}),
        json.dumps({"type": "pipeline_event", "stage": "nunca_deberia_llegar"}),
    ])

    events = [ev async for ev in client.receive()]

    assert [e.type for e in events] == ["pipeline_event"]


def test_receive_termina_en_turn_end_sin_esperar_done() -> None:
    asyncio.run(_test_receive_termina_en_turn_end_sin_esperar_done())


# ---------------------------------------------------------------------------
# Issue #15: connect() debe esperar el mensaje 'ready' antes de retornar.
#
# Sin esto, si el gateway rechaza el handshake (p.ej. client_key inválida),
# el cliente envía todo el turno de audio a una conexión muerta, descubriendo
# el error tarde y de forma genérica al final del turno.
# ---------------------------------------------------------------------------


async def _test_connect_espera_mensaje_ready_antes_de_retornar() -> None:
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws_mock = MagicMock()
    ws_mock.send = AsyncMock()
    ws_mock.recv = AsyncMock(return_value=json.dumps({"type": "ready"}))
    sys.modules["websockets"].connect = AsyncMock(return_value=ws_mock)

    client = GatewayClient(cfg)
    await client.connect()

    ws_mock.recv.assert_awaited_once()
    ws_mock.send.assert_awaited_once()


async def _test_connect_levanta_si_primer_mensaje_es_error() -> None:
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws_mock = MagicMock()
    ws_mock.send = AsyncMock()
    ws_mock.recv = AsyncMock(return_value=json.dumps({"type": "error", "message": "bad key"}))
    sys.modules["websockets"].connect = AsyncMock(return_value=ws_mock)

    client = GatewayClient(cfg)
    try:
        await client.connect()
    except RuntimeError as exc:
        assert "bad key" in str(exc), f"Mensaje del error no propagado: {exc}"
    else:
        raise AssertionError("connect() debería haber lanzado RuntimeError por mensaje 'error'")


async def _test_connect_levanta_si_primer_mensaje_tipo_inesperado() -> None:
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws_mock = MagicMock()
    ws_mock.send = AsyncMock()
    ws_mock.recv = AsyncMock(return_value=json.dumps({"type": "transcription"}))
    sys.modules["websockets"].connect = AsyncMock(return_value=ws_mock)

    client = GatewayClient(cfg)
    try:
        await client.connect()
    except RuntimeError as exc:
        assert "transcription" in str(exc), f"Tipo inesperado no propagado: {exc}"
    else:
        raise AssertionError("connect() debería haber lanzado RuntimeError por tipo inesperado")


async def _test_connect_propagar_connection_closed_si_server_cierra_antes_de_ready() -> None:
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws_mock = MagicMock()
    ws_mock.send = AsyncMock()
    ws_mock.recv = AsyncMock(side_effect=_FakeConnectionClosed(1008))
    sys.modules["websockets"].connect = AsyncMock(return_value=ws_mock)

    client = GatewayClient(cfg)
    try:
        await client.connect()
    except _FakeConnectionClosed:
        pass
    else:
        raise AssertionError("connect() debería propagar ConnectionClosed del server")


async def _test_connect_levanta_timeout_si_server_no_responde_ready() -> None:
    """El recv() del handshake respeta cfg.connect_timeout_s: si el gateway
    no envía ready a tiempo, asyncio.TimeoutError se loguea y se re-lanza.
    Pinea la rama del except (sin capturarla genéricamente como
    ConnectionClosed) para que un cambio accidental futuro no la trague."""
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=0.05)

    async def _slow_recv() -> str:
        await asyncio.sleep(1.0)
        return json.dumps({"type": "ready"})

    ws_mock = MagicMock()
    ws_mock.send = AsyncMock()
    ws_mock.recv = _slow_recv
    sys.modules["websockets"].connect = AsyncMock(return_value=ws_mock)

    client = GatewayClient(cfg)
    try:
        await client.connect()
    except asyncio.TimeoutError:
        pass
    else:
        raise AssertionError("connect() debería haber levantado TimeoutError esperando ready")


async def _test_connect_propagar_json_invalido_en_respuesta_handshake() -> None:
    """Si el primer mensaje no es JSON (p.ej. proxy/LB devolviendo HTML 502
    antes del WS), connect() propaga json.JSONDecodeError sin capturarla.
    Es la política 'fail fast' del handshake, deliberadamente distinta del
    'tolerant skip' de receive() — un cambio accidental aquí debe romper este
    test, no degradar silenciosamente."""
    cfg = GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=1.0)
    ws_mock = MagicMock()
    ws_mock.send = AsyncMock()
    ws_mock.recv = AsyncMock(return_value="<html>502 Bad Gateway</html>")
    sys.modules["websockets"].connect = AsyncMock(return_value=ws_mock)

    client = GatewayClient(cfg)
    try:
        await client.connect()
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("connect() debería propagar JSONDecodeError del handshake")


def test_connect_espera_mensaje_ready_antes_de_retornar() -> None:
    asyncio.run(_test_connect_espera_mensaje_ready_antes_de_retornar())


def test_connect_levanta_si_primer_mensaje_es_error() -> None:
    asyncio.run(_test_connect_levanta_si_primer_mensaje_es_error())


def test_connect_levanta_si_primer_mensaje_tipo_inesperado() -> None:
    asyncio.run(_test_connect_levanta_si_primer_mensaje_tipo_inesperado())


def test_connect_propagar_connection_closed_si_server_cierra_antes_de_ready() -> None:
    asyncio.run(_test_connect_propagar_connection_closed_si_server_cierra_antes_de_ready())


def test_connect_levanta_timeout_si_server_no_responde_ready() -> None:
    asyncio.run(_test_connect_levanta_timeout_si_server_no_responde_ready())


def test_connect_propagar_json_invalido_en_respuesta_handshake() -> None:
    asyncio.run(_test_connect_propagar_json_invalido_en_respuesta_handshake())


# ---------------------------------------------------------------------------
# Issue #16: receive() debe distinguir cierre limpio (ConnectionClosedOK,
# código 1000/1001) de cierre anómalo (ConnectionClosedError, 1006/1009/...).
# El actual `except ConnectionClosed` traga ambos casos, así que un cierre
# anómalo a mitad de turno es invisible para state_machine — el bug que
# cierra la issue.
# ---------------------------------------------------------------------------


class _FakeWSRaiseAfter:
    """Async-iterable que emite `messages` y luego lanza `exc`.

    Suficiente para probar que receive() distingue ConnectionClosedOK de
    ConnectionClosedError: el primero debe terminar en silencio, el segundo
    debe propagar."""

    def __init__(self, messages: list, exc: BaseException) -> None:
        self._messages = list(messages)
        self._exc = exc

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._messages:
            return self._messages.pop(0)
        raise self._exc


async def _test_receive_propagates_connection_closed_error() -> None:
    """Si el servidor cae a mitad de turno (ej. 1006 abnormal closure, 1009
    mensaje >max_size, 1011 internal error), websockets lanza
    ConnectionClosedError — receive() debe propagarlo para que state_machine
    lo vea en receive_task.exception() y publique VoiceEvent(type='error').

    Antes del fix, el `except ConnectionClosed` genérico de receive() lo
    tragaba (junto con el OK), y el turno terminaba con playback_ended como
    si todo hubiera ido bien."""
    from websockets.exceptions import ConnectionClosed, ConnectionClosedError
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    pcm = b"\x00\x01" * 100
    frame = bytes([0xA1, 0x00, 0x01]) + pcm  # turn_seq=1 + PCM
    exc = ConnectionClosedError("simulated server drop")
    assert isinstance(exc, ConnectionClosed)  # sanity: jerarquía correcta
    client._ws = _FakeWSRaiseAfter([frame], exc)

    try:
        events = [ev async for ev in client.receive()]
    except ConnectionClosedError:
        # Lo que esperamos: que la excepción se propague, no que se trague.
        pass
    else:
        raise AssertionError(
            "receive() debería propagar ConnectionClosedError (cierre anómalo "
            "del servidor), pero terminó silenciosamente como si fuera un "
            f"fin de turno normal. Eventos recibidos: {events!r}"
        )


async def _test_receive_silently_terminates_on_connection_closed_ok() -> None:
    """Regression: cierre limpio (1000/1001) tras los eventos del turno NO
    debe propagarse — state_machine ya cerró el turno vía turn_end, así que
    un OK recibido tras eso es ruido benigno (la sesión puede continuar en
    otro turno). Antes del fix esto funcionaba por accidente (el except
    genérico tragaba TODO); el fix debe preservarlo explícitamente."""
    from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    pcm = b"\x00\x01" * 100
    frame = bytes([0xA1, 0x00, 0x01]) + pcm
    ok_exc = ConnectionClosedOK("simulated clean close")
    assert isinstance(ok_exc, ConnectionClosed) and not isinstance(ok_exc, ConnectionClosedError)  # sanity
    client._ws = _FakeWSRaiseAfter([frame], ok_exc)

    # No debe lanzar: el cierre limpio termina el generador en silencio,
    # como hace con turn_end (issue #15 fix preservó ese comportamiento).
    events = [ev async for ev in client.receive()]
    assert len(events) == 1
    assert events[0].type == "tts_chunk"
    assert events[0].data["audio"] == pcm


def test_receive_propagates_connection_closed_error() -> None:
    asyncio.run(_test_receive_propagates_connection_closed_error())


def test_receive_silently_terminates_on_connection_closed_ok() -> None:
    asyncio.run(_test_receive_silently_terminates_on_connection_closed_ok())
