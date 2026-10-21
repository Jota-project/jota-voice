"""pipeline_event del gateway (prueba real 2026-10-21).

jota-gateway (src/services/pipeline_tracker.py) manda
``{"type": "pipeline_event", "stage", "elapsed_ms", "turn"}`` cuando el
handshake pide ``status``. Antes cada uno salía como "evento desconocido"
(~20 líneas DEBUG por turno). Sin red ni esperas reales.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from app.playback_engine import PlaybackEngine
from backends.gateway_client import GatewayClient, GatewayEvent
from config import GatewayConfig
from domain import state_machine
from domain.event_bus import EventBus, VoiceEvent
from domain.gateway_session import gateway_idle_listener
from domain.pipeline_stats import TurnLatency, publish_pipeline_stage


def _pe(stage: str, elapsed_ms: float | None = 0, turn: int | None = 1) -> GatewayEvent:
    return GatewayEvent(type="pipeline_event", data={
        "stage": stage, "elapsed_ms": elapsed_ms, "turn": turn,
    })


def _drain(sub) -> list[VoiceEvent]:
    out = []
    while not sub._q.empty():
        out.append(sub._q.get_nowait())
    return out


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _gcfg(**kw) -> GatewayConfig:
    base = dict(host="h", client_key="k", turn_end_grace_s=0.01)
    base.update(kw)
    return GatewayConfig(**base)


def _streaming_gateway(events, clock: _FakeClock, step_s: float):
    """receive() que avanza el reloj falso ``step_s`` antes de cada evento."""
    async def _receive():
        for ev in events:
            clock.now += step_s
            yield ev
        await asyncio.Event().wait()  # el WS real no se cierra tras el turno

    gateway = MagicMock(spec=GatewayClient)
    gateway.receive = _receive
    return gateway


# ---------------------------------------------------------------------------
# TurnLatency
# ---------------------------------------------------------------------------


def test_latencias_suman_elapsed_desde_transcription_final() -> None:
    """elapsed_ms es el tiempo desde el evento ANTERIOR del tracker (de
    cualquier etapa), así que las marcas se acumulan desde el STT final."""
    lat = TurnLatency()
    lat.feed("transcription_partial", 300, now=1.0)  # antes del final: no cuenta
    lat.feed("transcription_final", 120, now=2.0)
    lat.feed("tts_start", 40, now=2.1)
    lat.feed("llm_start", 10, now=2.2)
    lat.feed("llm_first_token", 800, now=3.0)
    lat.feed("tts_first_chunk", 600, now=3.6)
    lat.feed("llm_done", 1000, now=4.6)
    lat.feed("tts_done", 5000, now=9.6)
    line = lat.summary(now=12.0)
    assert line is not None
    assert "1º token LLM 850" in line
    assert "1º chunk TTS 1450" in line
    assert "fin TTS 7450" in line
    assert "fin turno 10000" in line  # reloj del cliente: 12.0 - 2.0 s


def test_latencias_etapas_ausentes_salen_como_raya() -> None:
    lat = TurnLatency()
    lat.feed("transcription_final", 0, now=0.0)
    lat.feed("llm_first_token", 500, now=0.5)
    line = lat.summary(now=1.0)
    assert line is not None
    assert "1º token LLM 500" in line and "1º chunk TTS —" in line


def test_latencias_sin_stt_final_no_hay_linea() -> None:
    lat = TurnLatency()
    lat.feed("llm_start", 10, now=0.0)
    assert lat.summary(now=1.0) is None


def test_latencias_elapsed_ausente_no_rompe_la_suma() -> None:
    lat = TurnLatency()
    lat.feed("transcription_final", 0, now=0.0)
    lat.feed("llm_start", None, now=0.1)
    lat.feed("llm_first_token", 300, now=0.4)
    line = lat.summary(now=1.0)
    assert line is not None and "1º token LLM 300" in line


def test_latencias_solo_cuenta_la_primera_vez_de_cada_etapa() -> None:
    lat = TurnLatency()
    lat.feed("transcription_final", 0, now=0.0)
    lat.feed("llm_first_token", 200, now=0.2)
    lat.feed("llm_first_token", 900, now=1.1)
    line = lat.summary(now=2.0)
    assert line is not None and "1º token LLM 200" in line


# ---------------------------------------------------------------------------
# publish_pipeline_stage
# ---------------------------------------------------------------------------


def test_publica_pipeline_stage_en_el_bus() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    publish_pipeline_stage(bus, {"stage": "llm_start", "elapsed_ms": 5.0, "turn": 2}, "RESPONDING")
    events = _drain(sub)
    assert [(e.type, e.data) for e in events] == [
        ("pipeline_stage", {"stage": "llm_start", "elapsed_ms": 5.0, "turn": 2}),
    ]


@pytest.mark.parametrize("stage", ["client_slow", "session_revoked"])
def test_client_slow_y_session_revoked_son_warning(stage, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="domain.pipeline_stats")
    publish_pipeline_stage(EventBus(), {"stage": stage, "elapsed_ms": 1.0, "turn": 1}, "IDLE")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and stage in warnings[0].getMessage()


def test_etapas_normales_no_escriben_en_info(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="domain.pipeline_stats")
    for stage in ("llm_start", "tts_first_chunk", "barge_in", "session_end"):
        publish_pipeline_stage(EventBus(), {"stage": stage, "elapsed_ms": 1.0, "turn": 1}, "X")
    assert not [r for r in caplog.records if r.levelno >= logging.INFO]


# ---------------------------------------------------------------------------
# RESPONDING
# ---------------------------------------------------------------------------


def _turno_real() -> list[GatewayEvent]:
    """Orden real del gateway (bridge.py): transcription_final va antes del
    `transcription`; tts_start y llm_start después del turn_start."""
    return [
        _pe("transcription_partial", 200, turn=0),
        _pe("transcription_final", 100, turn=0),
        GatewayEvent(type="transcription", data={"text": "pon música"}),
        GatewayEvent(type="turn_start", data={"turn_id": "t-1", "turn_seq": 1}),
        _pe("tts_start", 30),
        _pe("llm_start", 10),
        GatewayEvent(type="llm_token", data={"turn_id": "t-1", "content": "Vale."}),
        _pe("llm_first_token", 700),
        _pe("llm_done", 50),
        _pe("tts_first_chunk", 400),
        GatewayEvent(type="tts_chunk", data={"audio": b"\x01\x00", "turn_seq": 1}),
        _pe("tts_done", 900),
        GatewayEvent(type="turn_end", data={"turn_id": "t-1"}),
    ]


@pytest.mark.asyncio
async def test_responding_no_trata_pipeline_event_como_desconocido(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    clock = _FakeClock()
    bus = EventBus()
    sub = bus.subscribe()
    gateway = _streaming_gateway(_turno_real(), clock, step_s=0.1)
    await asyncio.wait_for(state_machine._responding(
        bus, gateway, MagicMock(spec=PlaybackEngine), asyncio.Event(),
        gateway_cfg=_gcfg(), clock=clock,
    ), 2.0)

    assert not [r for r in caplog.records if "desconocido" in r.getMessage()]
    stages = [e.data["stage"] for e in _drain(sub) if e.type == "pipeline_stage"]
    assert stages == [
        "transcription_partial", "transcription_final", "tts_start", "llm_start",
        "llm_first_token", "llm_done", "tts_first_chunk", "tts_done",
    ]
    # UNA línea INFO de latencias por turno, con los tiempos del gateway.
    lines = [r for r in caplog.records
             if r.levelno == logging.INFO and "Latencias" in r.getMessage()]
    assert len(lines) == 1
    msg = lines[0].getMessage()
    assert "1º token LLM 740" in msg and "1º chunk TTS 1190" in msg and "fin TTS 2090" in msg


@pytest.mark.asyncio
async def test_responding_cancelado_tambien_deja_su_linea_de_latencias(caplog) -> None:
    caplog.set_level(logging.INFO)
    events = [
        _pe("transcription_final", 100, turn=0),
        GatewayEvent(type="transcription", data={"text": "hola"}),
        GatewayEvent(type="turn_start", data={"turn_id": "t-1", "turn_seq": 1}),
        _pe("llm_start", 10),
        GatewayEvent(type="interrupted", data={}),
    ]
    gateway = _streaming_gateway(events, _FakeClock(), step_s=0)
    with pytest.raises(state_machine._TurnCancelled):
        await asyncio.wait_for(state_machine._responding(
            EventBus(), gateway, MagicMock(spec=PlaybackEngine), asyncio.Event(),
            gateway_cfg=_gcfg(),
        ), 2.0)
    lines = [r for r in caplog.records if "Latencias" in r.getMessage()]
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_actividad_del_pipeline_en_nuestro_turno_reinicia_el_plazo() -> None:
    """Con nuestro turno empezado, llm_*/tts_* prueban que el gateway trabaja
    en él: cuatro huecos de 25 s con plazo de 30 s no deben cortar el turno
    (sin contarlos como progreso, el plazo vencería al segundo hueco)."""
    clock = _FakeClock()
    events = [
        GatewayEvent(type="transcription", data={"text": "hola"}),
        GatewayEvent(type="turn_start", data={"turn_id": "t-1", "turn_seq": 1}),
        _pe("tts_start"), _pe("llm_start"), _pe("llm_first_token"), _pe("tts_first_chunk"),
        GatewayEvent(type="tts_chunk", data={"audio": b"\x01\x00", "turn_seq": 1}),
        GatewayEvent(type="turn_end", data={"turn_id": "t-1"}),
    ]
    gateway = _streaming_gateway(events, clock, step_s=25)
    playback = MagicMock(spec=PlaybackEngine)
    await asyncio.wait_for(state_machine._responding(
        EventBus(), gateway, playback, asyncio.Event(),
        gateway_cfg=_gcfg(response_start_timeout_s=30, turn_inactivity_timeout_s=30),
        clock=clock,
    ), 2.0)
    playback.play_chunk.assert_awaited_once()
    gateway.send_cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_event_antes_de_nuestro_turno_no_alarga_el_plazo_de_inicio() -> None:
    """Antes de nuestro turn_start, un llm_start solo puede ser de un push
    ajeno (el gateway manda turn_start antes de llm_start/tts_start): no debe
    alargar response_start_timeout_s."""
    clock = _FakeClock()
    events = [_pe("llm_start"), _pe("llm_first_token"), _pe("tts_start"), _pe("llm_done")]
    gateway = _streaming_gateway(events, clock, step_s=20)
    with pytest.raises(state_machine._ResponseTimeout) as info:
        await asyncio.wait_for(state_machine._responding(
            EventBus(), gateway, MagicMock(spec=PlaybackEngine), asyncio.Event(),
            gateway_cfg=_gcfg(response_start_timeout_s=30, turn_inactivity_timeout_s=30),
            clock=clock,
        ), 2.0)
    assert info.value.extra["phase"] == "start"


@pytest.mark.asyncio
async def test_pipeline_event_no_alarga_la_gracia_tras_turn_end() -> None:
    """Tras nuestro turn_end solo el audio alarga la gracia: un pipeline_event
    (session_end, tts_done) no debe retener RESPONDING."""
    clock = _FakeClock()
    events = [
        GatewayEvent(type="transcription", data={"text": "hola"}),
        GatewayEvent(type="turn_start", data={"turn_id": "t-1", "turn_seq": 1}),
        GatewayEvent(type="turn_end", data={"turn_id": "t-1"}),
        _pe("tts_done"),
        _pe("tts_done"),
    ]
    gateway = _streaming_gateway(events, clock, step_s=2.5)
    bus = EventBus()
    sub = bus.subscribe()
    await asyncio.wait_for(state_machine._responding(
        bus, gateway, MagicMock(spec=PlaybackEngine), asyncio.Event(),
        gateway_cfg=_gcfg(turn_end_grace_s=2.0), clock=clock,
    ), 2.0)
    # Gracia de 2 s: el primer tts_done se lee mientras la gracia sigue
    # abierta y llega a 2,5 s del turn_end; el segundo ya no se lee porque
    # la gracia no se reinició.
    stages = [e.data["stage"] for e in _drain(sub) if e.type == "pipeline_stage"]
    assert stages == ["tts_done"]


# ---------------------------------------------------------------------------
# IDLE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idle_listener_publica_pipeline_stage_y_no_lo_ignora(caplog) -> None:
    caplog.set_level(logging.DEBUG)

    async def _receive():
        yield _pe("session_revoked", 3, turn=4)
        yield _pe("session_end", 1, turn=4)

    gateway = MagicMock(spec=GatewayClient)
    gateway.receive = _receive
    bus = EventBus()
    sub = bus.subscribe()
    assert await gateway_idle_listener(bus, gateway) == "ended"
    events = [e for e in _drain(sub) if e.type == "pipeline_stage"]
    assert [e.data["stage"] for e in events] == ["session_revoked", "session_end"]
    assert not [r for r in caplog.records if "ignorado" in r.getMessage()]
    assert [r.levelno for r in caplog.records if "session_revoked" in r.getMessage()] == [
        logging.WARNING
    ]
