"""Fase R — sesión persistente robusta (issues #118, #116, #103, #107).

Tests sin red: un FakeGateway con la misma interfaz que GatewayClient
(is_connected, ensure_connected, receive, send_*, last_close, fatal_error).
Los tests contra un servidor WebSocket real están en
tests/backends/test_gateway_real_ws.py.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backends.gateway_client import (
    GatewayDisconnected,
    GatewayEvent,
    GatewayHandshakeError,
)
from config import AudioConfig, Config, DeviceConfig, GatewayConfig
from domain import gateway_session
from domain.event_bus import EventBus, VoiceEvent


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeGateway:
    """Gateway programable. ``sessions`` es una lista de guiones, uno por
    conexión: cada guion es una lista de GatewayEvent o una excepción final
    (GatewayDisconnected) que cierra la sesión. ``connect_results`` permite
    hacer fallar connects concretos."""

    def __init__(self, *, connected: bool = True, sessions=None, connect_results=None):
        self._connected = connected
        self.sessions = list(sessions or [])
        self.connect_results = list(connect_results or [])
        self.connect_calls = 0
        self.receive_calls = 0
        self.readers = 0
        self.max_readers = 0
        self.last_close: GatewayDisconnected | None = None
        self.fatal_error: GatewayHandshakeError | None = None
        self.keepalives = 0
        self.sent: list = []
        self._since_send = 0.0
        self._session_id = "s-0"
        self._script: list = []

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def seconds_since_last_send(self) -> float:
        return self._since_send

    async def ensure_connected(self) -> None:
        if self._connected:
            return
        self.connect_calls += 1
        if self.connect_results:
            res = self.connect_results.pop(0)
            if isinstance(res, BaseException):
                if isinstance(res, GatewayHandshakeError) and res.fatal:
                    self.fatal_error = res
                raise res
        self._connected = True
        self.fatal_error = None
        self._session_id = f"s-{self.connect_calls}"

    def close(self, code: int, reason: str = "") -> GatewayDisconnected:
        exc = GatewayDisconnected(code, reason)
        self._connected = False
        self.last_close = exc
        return exc

    async def receive(self):
        self.receive_calls += 1
        self.readers += 1
        self.max_readers = max(self.max_readers, self.readers)
        try:
            if not self._connected:
                raise self.last_close or GatewayDisconnected(None)
            script = self.sessions.pop(0) if self.sessions else None
            if script is None:
                await asyncio.Event().wait()  # sesión viva y en silencio
            for item in script:
                if isinstance(item, tuple) and item[0] == "close":
                    raise self.close(item[1], item[2] if len(item) > 2 else "")
                if item == "block":
                    await asyncio.Event().wait()
                yield item
        finally:
            self.readers -= 1

    async def send_keepalive(self) -> None:
        self.keepalives += 1
        self._since_send = 0.0

    async def send_audio(self, b: bytes) -> None:
        self.sent.append(("audio", b))

    async def send_end(self) -> None:
        self.sent.append(("end",))

    async def send_text(self, t: str) -> None:
        self.sent.append(("send", t))

    async def send_cancel(self) -> None:
        self.sent.append(("cancel",))

    async def disconnect(self) -> None:
        self._connected = False


def _gcfg(**kw) -> GatewayConfig:
    base = dict(
        host="127.0.0.1", client_key="k", connect_timeout_s=1.0,
        reconnect_backoff_s=[0.01, 0.02, 0.05], keepalive_interval_s=0,
    )
    base.update(kw)
    return GatewayConfig(**base)


async def _collect_until(bus: EventBus, pred, timeout: float = 2.0) -> list[VoiceEvent]:
    got: list[VoiceEvent] = []

    async def _loop():
        async for ev in bus.subscribe():
            got.append(ev)
            if pred(ev, got):
                return

    try:
        await asyncio.wait_for(_loop(), timeout)
    except asyncio.TimeoutError:
        pass
    return got


async def _run_supervisor(cfg, bus, gw, seconds: float) -> None:
    t = asyncio.create_task(gateway_session.idle_supervisor(cfg, bus, gw))
    await asyncio.sleep(seconds)
    t.cancel()
    await asyncio.wait([t])
    assert t.cancelled(), f"el supervisor terminó solo: {t.exception() if t.done() else ''}"


# ---------------------------------------------------------------------------
# idle_supervisor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_publica_status_en_idle():
    bus = EventBus()
    gw = FakeGateway(sessions=[[GatewayEvent("status", {"service": "tts", "state": "unavailable"}), "block"]])
    sub = asyncio.create_task(_collect_until(bus, lambda e, _: e.type == "gateway_status", 1.0))
    await asyncio.sleep(0)
    await _run_supervisor(_gcfg(), bus, gw, 0.1)
    got = await sub
    assert got and got[-1].data["service"] == "tts"


@pytest.mark.asyncio
async def test_supervisor_reconecta_al_instante_tras_cierre_1000_por_inactividad(monkeypatch):
    """#116/#118: el gateway cierra con 1000 a los 300 s sin aviso. Es un
    cierre esperado: se reabre la sesión sin esperar backoff."""
    monkeypatch.setattr(gateway_session, "_STABLE_SESSION_S", 0.0)
    bus = EventBus()
    gw = FakeGateway(sessions=[[("close", 1000, "")], None])
    await _run_supervisor(_gcfg(reconnect_backoff_s=[5.0]), bus, gw, 0.2)
    assert gw.connect_calls == 1, "debía reconectar sin esperar los 5 s de backoff"
    assert gw.is_connected


@pytest.mark.parametrize("code", [1001, 1006, 1011, 1012, 1013])
@pytest.mark.asyncio
async def test_supervisor_reconecta_con_backoff_tras_cierres_recuperables(code):
    bus = EventBus()
    gw = FakeGateway(
        sessions=[[("close", code, "x")], None],
        connect_results=[OSError("connection refused"), OSError("connection refused")],
    )
    errors = asyncio.create_task(_collect_until(bus, lambda e, _: False, 0.5))
    await asyncio.sleep(0)
    await _run_supervisor(_gcfg(), bus, gw, 0.4)
    got = await errors
    assert gw.connect_calls == 3 and gw.is_connected
    # Solo el primer fallo de la racha va como `error` (no inundar la UI).
    errs = [e for e in got if e.type == "error"]
    assert len(errs) == 1 and errs[0].data["code"] == "GATEWAY_DISCONNECTED"
    disc = [e for e in got if e.type == "gateway_status" and e.data.get("state") == "disconnected"]
    assert disc and disc[0].data["code"] == code


@pytest.mark.asyncio
async def test_supervisor_no_reintenta_en_bucle_tras_1008():
    """#118/#107: 1008 (auth/agente) es fatal: un único error visible y sin
    reintentos automáticos."""
    bus = EventBus()
    fatal = GatewayHandshakeError("invalid_client_key", 1008, "Clave de cliente invalida o inactiva")
    gw = FakeGateway(sessions=[[("close", 1000, "")]], connect_results=[fatal, fatal, fatal])
    errors = asyncio.create_task(_collect_until(bus, lambda e, _: False, 0.5))
    await asyncio.sleep(0)
    await _run_supervisor(_gcfg(), bus, gw, 0.4)
    got = await errors
    assert gw.connect_calls == 1, f"reintentó {gw.connect_calls} veces un rechazo fatal"
    errs = [e for e in got if e.type == "error"]
    assert len(errs) == 1
    assert errs[0].data["code"] == "GATEWAY_HANDSHAKE_REJECTED"
    assert errs[0].data["kind"] == "invalid_client_key"
    assert errs[0].data["fatal"] is True


@pytest.mark.asyncio
async def test_supervisor_cierre_1008_a_mitad_de_sesion_tampoco_reintenta():
    bus = EventBus()
    gw = FakeGateway(sessions=[[("close", 1008, "policy")]])
    await _run_supervisor(_gcfg(), bus, gw, 0.2)
    assert gw.connect_calls == 0


@pytest.mark.asyncio
async def test_supervisor_agent_not_available_si_reintenta():
    """#107: agent_not_available es transitorio (roster vacío en un
    reconnect del gateway): se reintenta con backoff."""
    bus = EventBus()
    transient = GatewayHandshakeError("agent_not_available", 1008, "Agent 'main' not available.")
    gw = FakeGateway(connected=False, connect_results=[transient, transient])
    await _run_supervisor(_gcfg(), bus, gw, 0.3)
    assert gw.connect_calls == 3 and gw.is_connected


@pytest.mark.asyncio
async def test_supervisor_sin_bucle_de_errores_si_el_listener_falla():
    """#118 "posible bucle de errores": un fallo inesperado del lector no
    debe relanzarse en caliente publicando un error tras otro."""
    bus = EventBus()
    gw = FakeGateway()

    async def _boom():
        raise RuntimeError("kaput")
        yield  # noqa

    gw.receive = _boom
    errors = asyncio.create_task(_collect_until(bus, lambda e, _: False, 0.3))
    await asyncio.sleep(0)
    await _run_supervisor(_gcfg(reconnect_backoff_s=[0.1]), bus, gw, 0.3)
    got = await errors
    assert len([e for e in got if e.type == "error"]) <= 4


@pytest.mark.asyncio
async def test_supervisor_sesion_que_muere_al_abrir_escala_el_backoff():
    bus = EventBus()
    gw = FakeGateway(sessions=[[("close", 1000, "")]] * 50)
    await _run_supervisor(_gcfg(reconnect_backoff_s=[0.05, 0.1, 0.2]), bus, gw, 0.4)
    assert gw.connect_calls <= 5, f"martilleó al gateway: {gw.connect_calls} connects en 0.4 s"


@pytest.mark.asyncio
async def test_supervisor_relee_tras_turn_end_de_un_push():
    """Antes, el listener de IDLE moría al terminar un push (receive() acaba
    en turn_end) y nadie leía el socket hasta el siguiente turno."""
    bus = EventBus()
    gw = FakeGateway(sessions=[
        [GatewayEvent("turn_start", {"turn_seq": 1})],  # receive() termina (turn_end)
        [GatewayEvent("status", {"service": "orchestrator", "state": "restored"}), "block"],
    ])
    sub = asyncio.create_task(_collect_until(bus, lambda e, _: e.type == "gateway_status", 1.0))
    await asyncio.sleep(0)
    await _run_supervisor(_gcfg(), bus, gw, 0.3)
    got = await sub
    assert any(e.data.get("service") == "orchestrator" for e in got)


@pytest.mark.asyncio
async def test_supervisor_manda_keepalive_si_el_cliente_lleva_tiempo_callado():
    """#116: el gateway cierra a los IDLE_TIMEOUT_S sin mensajes del cliente."""
    bus = EventBus()
    gw = FakeGateway()
    gw._since_send = 999.0
    await _run_supervisor(_gcfg(keepalive_interval_s=0.05), bus, gw, 0.2)
    assert gw.keepalives >= 1


@pytest.mark.asyncio
async def test_supervisor_keepalive_desactivado_con_cero():
    bus = EventBus()
    gw = FakeGateway()
    gw._since_send = 999.0
    await _run_supervisor(_gcfg(keepalive_interval_s=0), bus, gw, 0.1)
    assert gw.keepalives == 0


# ---------------------------------------------------------------------------
# Reparto del socket: un único lector (sustituye a los tests del
# _idle_coordinator, que probaban una copia del coordinator escrita dentro
# del propio test).
# ---------------------------------------------------------------------------


def _cfg(**gw) -> Config:
    return Config(
        gateway=_gcfg(**gw),
        device=DeviceConfig(id="t"),
        audio=AudioConfig(silence_timeout_s=0.1, recording_timeout_s=0.3),
    )


@pytest.mark.asyncio
async def test_idle_cancela_el_supervisor_antes_de_volver():
    from domain.state_machine import _idle

    bus = EventBus()
    audio = MagicMock()
    audio.get_queue.return_value = asyncio.Queue()
    gw = FakeGateway()  # sesión viva y en silencio: el supervisor lee
    cfg = _cfg()

    t = asyncio.create_task(_idle(
        cfg, bus, audio, asyncio.Event(),
        lambda: gateway_session.idle_supervisor(cfg.gateway, bus, gw),
    ))
    await asyncio.sleep(0.05)
    assert gw.readers == 1, "en IDLE el supervisor debe estar leyendo"
    bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok"}))
    assert await asyncio.wait_for(t, 1.0) == "ok"
    assert gw.readers == 0, "al salir de IDLE nadie debe quedar leyendo el socket"


@pytest.mark.asyncio
async def test_un_solo_lector_del_socket_durante_un_turno_completo():
    """Regresión del coordinator: con un error publicado a mitad de turno
    (beep fallido) el viejo coordinator re-armaba el listener de IDLE y había
    dos lectores. Ahora el error no arranca nada."""
    from domain.state_machine import run as sm_run

    bus = EventBus()
    cfg = _cfg()
    audio = MagicMock()
    q: asyncio.Queue = asyncio.Queue()
    for _ in range(10):
        q.put_nowait(bytes(2048))
    audio.get_queue.return_value = q
    audio.get_preroll.return_value = b""
    audio.is_silence.return_value = True
    gw = FakeGateway(sessions=[
        ["block"],  # IDLE: supervisor lee y bloquea
        [GatewayEvent("transcription", {"text": "hola"}),
         GatewayEvent("tts_chunk", {"audio": b"\x00\x00"})],  # turno
    ])
    playback = MagicMock()
    playback.play_chunk = AsyncMock()
    playback.drain = AsyncMock()
    playback.play_notification = AsyncMock(side_effect=OSError("beep roto"))

    sm = asyncio.create_task(sm_run(
        cfg, bus, audio, gw, playback,
        idle_background=lambda: gateway_session.idle_supervisor(cfg.gateway, bus, gw),
    ))
    await asyncio.sleep(0.05)
    sub = asyncio.create_task(_collect_until(bus, lambda e, _: e.type == "playback_ended", 3.0))
    await asyncio.sleep(0)
    bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok"}))
    got = await sub
    sm.cancel()
    await asyncio.wait([sm])
    types = [e.type for e in got]
    assert "error" in types and "playback_ended" in types, types
    assert gw.max_readers == 1, f"hubo {gw.max_readers} lectores simultáneos del socket"


# ---------------------------------------------------------------------------
# Reconexión perezosa al empezar el turno (sustituye al test que fijaba
# GATEWAY_DISCONNECTED sin reconectar) y cierre a mitad de turno.
# ---------------------------------------------------------------------------


def _audio_with_frames(n: int = 5) -> MagicMock:
    audio = MagicMock()
    q: asyncio.Queue = asyncio.Queue()
    for _ in range(n):
        q.put_nowait(bytes(2048))
    audio.get_queue.return_value = q
    audio.get_preroll.return_value = b""
    audio.is_silence.return_value = True
    return audio


def _playback() -> MagicMock:
    p = MagicMock()
    p.play_chunk = AsyncMock()
    p.drain = AsyncMock()
    p.play_notification = AsyncMock()
    return p


@pytest.mark.asyncio
async def test_recording_reconecta_si_la_sesion_esta_caida():
    from domain.state_machine import _recording

    bus = EventBus()
    gw = FakeGateway(connected=False)
    gw.last_close = GatewayDisconnected(1000)
    # Simula el audio dicho mientras se reconecta: los frames encolados
    # DESPUÉS del drenaje inicial se envían tras el handshake.
    audio = _audio_with_frames(0)
    orig = gw.ensure_connected

    async def _connect_and_speak():
        await orig()
        for _ in range(5):
            audio.get_queue.return_value.put_nowait(bytes(2048))

    gw.ensure_connected = _connect_and_speak
    await _recording("ok", bus, audio, gw, _playback(), _cfg(), asyncio.Event())
    assert gw.connect_calls == 1
    assert ("end",) in gw.sent
    assert any(s[0] == "audio" for s in gw.sent)


@pytest.mark.asyncio
async def test_recording_reconexion_fallida_da_error_visible_sin_enviar_audio():
    from domain.state_machine import _GatewayError, _recording

    bus = EventBus()
    gw = FakeGateway(connected=False, connect_results=[OSError("Connection refused")])
    with pytest.raises(_GatewayError) as info:
        await _recording("ok", bus, _audio_with_frames(), gw, _playback(), _cfg(), asyncio.Event())
    assert info.value.code == "GATEWAY_DISCONNECTED"
    assert "Connection refused" in info.value.message
    assert gw.sent == []


@pytest.mark.asyncio
async def test_recording_rechazo_fatal_lleva_kind_al_bus():
    """#107: el usuario ve QUÉ falló (client_key vs agente)."""
    from domain.state_machine import run as sm_run

    bus = EventBus()
    fatal = GatewayHandshakeError("agent_not_permitted", 1008, "Agent 'x' not permitted for this client.")
    gw = FakeGateway(connected=False, connect_results=[fatal])
    sm = asyncio.create_task(sm_run(_cfg(), bus, _audio_with_frames(), gw, _playback()))
    await asyncio.sleep(0.02)
    sub = asyncio.create_task(_collect_until(bus, lambda e, _: e.type == "error", 2.0))
    await asyncio.sleep(0)
    bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok"}))
    got = await sub
    sm.cancel()
    await asyncio.wait([sm])
    err = got[-1]
    assert err.data["code"] == "GATEWAY_HANDSHAKE_REJECTED"
    assert err.data["kind"] == "agent_not_permitted"
    assert err.data["fatal"] is True
    assert gw.sent == []


@pytest.mark.parametrize("code", [1000, 1001, 1006, 1013])
@pytest.mark.asyncio
async def test_cierre_a_mitad_de_turno_da_error_y_el_siguiente_turno_funciona(code):
    """#118: un turno en curso cuando cae la conexión termina con error
    visible (no con playback_ended) y el siguiente turno reconecta y va."""
    from domain.state_machine import run as sm_run

    bus = EventBus()
    gw = FakeGateway(sessions=[
        [GatewayEvent("transcription", {"text": "hola"}), ("close", code, "bye")],
        [GatewayEvent("transcription", {"text": "otra"}),
         GatewayEvent("tts_chunk", {"audio": b"\x00\x00"})],
    ])
    audio = _audio_with_frames(50)
    sm = asyncio.create_task(sm_run(_cfg(), bus, audio, gw, _playback()))
    await asyncio.sleep(0.02)

    sub = asyncio.create_task(_collect_until(bus, lambda e, _: e.type == "error", 3.0))
    await asyncio.sleep(0)
    bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok"}))
    first = await sub
    assert first[-1].type == "error"
    assert first[-1].data["code"] == "GATEWAY_DISCONNECTED"
    assert first[-1].data["close_code"] == code
    assert "playback_ended" not in [e.type for e in first]

    await asyncio.sleep(0.05)
    for _ in range(10):
        audio.get_queue.return_value.put_nowait(bytes(2048))
    sub2 = asyncio.create_task(_collect_until(
        bus, lambda e, _: e.type in ("playback_ended", "error"), 3.0))
    await asyncio.sleep(0)
    bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok"}))
    second = await sub2
    sm.cancel()
    await asyncio.wait([sm])
    assert second[-1].type == "playback_ended", [(e.type, e.data) for e in second]
    assert gw.connect_calls == 1


# ---------------------------------------------------------------------------
# #103: cancel simultáneo con cierre de conexión
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_simultaneo_con_cierre_informa_el_error_real():
    from domain.state_machine import _responding

    bus = EventBus()
    gw = FakeGateway()
    cancel_event = asyncio.Event()

    async def _receive():
        # El cancel y el cierre llegan en el mismo ciclo del loop.
        cancel_event.set()
        raise gw.close(1006)
        yield  # noqa

    gw.receive = _receive
    with pytest.raises(GatewayDisconnected) as info:
        await _responding(bus, gw, _playback(), cancel_event)
    assert info.value.code == 1006


@pytest.mark.asyncio
async def test_cancel_sin_error_sigue_siendo_cancel():
    from domain.state_machine import _responding, _TurnCancelled

    bus = EventBus()
    gw = FakeGateway(sessions=[["block"]])
    cancel_event = asyncio.Event()
    t = asyncio.create_task(_responding(bus, gw, _playback(), cancel_event))
    await asyncio.sleep(0.02)
    cancel_event.set()
    with pytest.raises(_TurnCancelled):
        await asyncio.wait_for(t, 1.0)
    assert ("cancel",) in gw.sent



# ---------------------------------------------------------------------------
# Drenaje de hijos que no se traga la cancelación exterior (mismo bug que
# _oww_loop / oww_client.run_forever): determinista con un hijo que tarda en
# procesar su cancel.
# ---------------------------------------------------------------------------


async def _slow_to_cancel(*_a, **_kw):
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await asyncio.sleep(0.2)  # limpieza lenta: ventana para el cancel exterior
        raise


@pytest.mark.asyncio
async def test_wait_wake_or_cancel_no_se_traga_la_cancelacion_exterior(monkeypatch):
    from domain import state_machine

    monkeypatch.setattr(state_machine, "_consume_wake", _slow_to_cancel)
    cancel_event = asyncio.Event()
    t = asyncio.create_task(state_machine._wait_wake_or_cancel(EventBus(), cancel_event, "X"))
    await asyncio.sleep(0.01)
    cancel_event.set()        # cancel_task gana → se empieza a drenar wake_task (lento)
    await asyncio.sleep(0.05)
    t.cancel()                # llega el cancel exterior durante el drenaje
    await asyncio.wait([t], timeout=1.0)
    assert t.done() and t.cancelled(), "la cancelación exterior se perdió"

