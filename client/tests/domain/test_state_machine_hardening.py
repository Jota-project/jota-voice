"""Fase 6 — hardening de la state machine (issues #62, #65, #66, #92, #93, #94).

Sin red ni esperas reales: mocks con spec y eventos del bus.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from app.playback_engine import PlaybackEngine
from backends.audio_base import AudioBackend
from backends.gateway_client import GatewayClient, GatewayDisconnected, GatewayEvent
from config import AudioConfig, Config, DeviceConfig, GatewayConfig, OWWConfig
from domain import state_machine
from domain.event_bus import EventBus, VoiceEvent


def _cfg(*, persistent_session: bool = True, **gateway) -> Config:
    return Config(
        gateway=GatewayConfig(
            host="127.0.0.1", client_key="k", persistent_session=persistent_session, **gateway
        ),
        device=DeviceConfig(id="test"),
    )


# ---------------------------------------------------------------------------
# Issue #66: los catch-all de _safe_send_cancel y _cleanup dejan rastro
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_send_cancel_registra_el_fallo(caplog) -> None:
    gateway = MagicMock(spec=GatewayClient)
    gateway.send_cancel.side_effect = RuntimeError("socket roto")
    with caplog.at_level(logging.DEBUG, logger="domain.state_machine"):
        await state_machine._safe_send_cancel(gateway)
    assert "socket roto" in caplog.text
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


@pytest.mark.asyncio
async def test_safe_send_cancel_con_sesion_cerrada_solo_debug(caplog) -> None:
    """Mandar cancel sobre una sesión ya cerrada es esperable: rastro en
    DEBUG, sin alarmar en WARNING."""
    gateway = MagicMock(spec=GatewayClient)
    gateway.send_cancel.side_effect = GatewayDisconnected(1006, "")
    with caplog.at_level(logging.DEBUG, logger="domain.state_machine"):
        await state_machine._safe_send_cancel(gateway)
    assert caplog.records
    assert all(r.levelno == logging.DEBUG for r in caplog.records)


@pytest.mark.asyncio
async def test_cleanup_registra_fallo_de_disconnect_y_resetea_playback(caplog) -> None:
    gateway = MagicMock(spec=GatewayClient)
    gateway.disconnect.side_effect = OSError("disconnect falló")
    playback = MagicMock(spec=PlaybackEngine)
    with caplog.at_level(logging.DEBUG, logger="domain.state_machine"):
        await state_machine._cleanup(_cfg(persistent_session=False), gateway, playback)
    assert "disconnect falló" in caplog.text
    playback.reset.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("persistent,disconnects", [(True, 0), (False, 1)])
async def test_cleanup_decide_por_la_config_no_por_atributos_privados(persistent, disconnects) -> None:
    """Antes leía gateway._cfg (atributo de instancia): con un mock con spec
    eso lanzaba AttributeError y el catch-all mudo lo escondía."""
    gateway = MagicMock(spec=GatewayClient)
    playback = MagicMock(spec=PlaybackEngine)
    await state_machine._cleanup(_cfg(persistent_session=persistent), gateway, playback)
    assert gateway.disconnect.await_count == disconnects


# ---------------------------------------------------------------------------
# Issue #92: la salida temprana de un async-for sobre el bus cierra la
# suscripción en el acto (aclosing), sin depender del finalizador de asyncio
# ---------------------------------------------------------------------------


def _wake_soon(bus: EventBus) -> None:
    asyncio.get_running_loop().call_soon(
        bus.publish, VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"})
    )


@pytest.mark.asyncio
async def test_consume_wake_retira_su_suscripcion_al_volver() -> None:
    bus = EventBus()
    _wake_soon(bus)
    assert await state_machine._consume_wake(bus) == "ok_nabu"
    # Sin ceder el loop: sin aclosing la cola seguiría registrada hasta que el
    # finalizador de async-generators de asyncio se ejecutara más tarde.
    assert bus._queues == []


@pytest.mark.asyncio
@pytest.mark.parametrize("idle_timeout", [0.0, 30.0])
async def test_wait_idle_retira_su_suscripcion_al_volver(idle_timeout) -> None:
    bus = EventBus()
    cfg = _cfg()
    cfg.oww = OWWConfig(idle_detection_timeout_s=idle_timeout)
    audio = MagicMock(spec=AudioBackend)
    audio.get_queue.return_value = asyncio.Queue()
    _wake_soon(bus)
    assert await state_machine._wait_idle(cfg, bus, audio, asyncio.Event()) == "ok_nabu"
    assert bus._queues == []


# ---------------------------------------------------------------------------
# Issue #62: un cancel que llega DURANTE el turno (aunque sea entre RECORDING
# y RESPONDING) no se pierde; uno de antes de la wake word sí se descarta
# ---------------------------------------------------------------------------


def _turn_audio() -> MagicMock:
    """Audio con 20 frames de silencio que llegan tras el drain de RECORDING."""
    audio = MagicMock(spec=AudioBackend)
    q: asyncio.Queue[bytes] = asyncio.Queue()

    def _preroll() -> bytes:
        for _ in range(20):
            q.put_nowait(bytes(2048))
        return b""

    audio.get_queue.return_value = q
    audio.get_preroll.side_effect = _preroll
    audio.is_silence.return_value = True
    return audio


def _turn_cfg() -> Config:
    cfg = _cfg()
    cfg.audio = AudioConfig(silence_timeout_s=0.05, recording_timeout_s=5.0)
    return cfg


async def _run_one_turn(bus, gateway, cancel_event, *, on_idle=None) -> list[VoiceEvent]:
    """Arranca run(), publica la wake word en el primer IDLE (llamando antes a
    ``on_idle``) y devuelve los eventos hasta el segundo IDLE."""
    events = bus.subscribe()
    got: list[VoiceEvent] = []
    sm = asyncio.create_task(state_machine.run(
        _turn_cfg(), bus, _turn_audio(), gateway, MagicMock(spec=PlaybackEngine), cancel_event,
    ))
    idles = 0
    try:
        async with asyncio.timeout(5.0):
            async for ev in events:
                got.append(ev)
                if ev.type == "state_changed" and ev.data.get("state") == "idle":
                    idles += 1
                    if idles == 2:
                        break
                    if on_idle is not None:
                        on_idle()
                    bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))
    finally:
        await events.aclose()
        sm.cancel()
        await asyncio.wait([sm])
    return got


def _respuesta_completa() -> list[GatewayEvent]:
    return [
        GatewayEvent(type="transcription", data={"text": "hola"}),
        GatewayEvent(type="tts_chunk", data={"audio": b"\x01\x00"}),
    ]


@pytest.mark.asyncio
async def test_cancel_entre_recording_y_responding_cancela_el_turno() -> None:
    """Antes RESPONDING hacía cancel_event.clear() al entrar: un /cancel que
    llegaba mientras se enviaba `end` (o sonaba el beep) se perdía y la
    respuesta se reproducía igualmente."""
    bus = EventBus()
    cancel_event = asyncio.Event()
    gateway = MagicMock(spec=GatewayClient)

    async def _receive():
        for ev in _respuesta_completa():
            yield ev

    gateway.receive = _receive
    gateway.send_end.side_effect = lambda: cancel_event.set()

    got = await _run_one_turn(bus, gateway, cancel_event)
    types = [e.type for e in got]
    assert {"type": "cancelled", "state": "RESPONDING"} in [
        {"type": e.type, **e.data} for e in got if e.type == "cancelled"
    ], types
    assert "playback_ended" not in types
    gateway.send_cancel.assert_awaited()


@pytest.mark.asyncio
async def test_cancel_de_antes_de_la_wake_word_no_aborta_el_turno() -> None:
    bus = EventBus()
    cancel_event = asyncio.Event()
    gateway = MagicMock(spec=GatewayClient)

    async def _receive():
        for ev in _respuesta_completa():
            yield ev

    gateway.receive = _receive
    got = await _run_one_turn(bus, gateway, cancel_event, on_idle=cancel_event.set)
    types = [e.type for e in got]
    assert "cancelled" not in types, types
    assert "playback_ended" in types, types
    gateway.send_cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_recording_y_responding_no_descartan_un_cancel_ya_activo() -> None:
    bus = EventBus()
    cancel_event = asyncio.Event()
    cancel_event.set()
    gateway, _ = _blocking_gateway()
    with pytest.raises(state_machine._TurnCancelled):
        await asyncio.wait_for(
            state_machine._responding(bus, gateway, MagicMock(spec=PlaybackEngine), cancel_event),
            1.0,
        )
    with pytest.raises(state_machine._TurnCancelled):
        await asyncio.wait_for(
            state_machine._recording(
                "ok_nabu", bus, _turn_audio(), gateway, MagicMock(spec=PlaybackEngine),
                _turn_cfg(), cancel_event,
            ),
            1.0,
        )


# ---------------------------------------------------------------------------
# Issue #93: RESPONDING espera el cancel en un solo sitio; la wake word es
# otra fuente de cancelación, sin un segundo cancel_event.wait()
# ---------------------------------------------------------------------------


def _blocking_gateway() -> tuple[MagicMock, asyncio.Event]:
    """Gateway cuyo receive() bloquea; ``reading`` se activa al entrar."""
    reading = asyncio.Event()

    async def _receive():
        reading.set()
        await asyncio.Event().wait()
        yield  # pragma: no cover

    gateway = MagicMock(spec=GatewayClient)
    gateway.receive = _receive
    return gateway, reading


@pytest.mark.asyncio
async def test_responding_espera_el_cancel_en_un_solo_sitio() -> None:
    bus = EventBus()
    gateway, reading = _blocking_gateway()
    cancel_event = asyncio.Event()
    t = asyncio.create_task(
        state_machine._responding(bus, gateway, MagicMock(spec=PlaybackEngine), cancel_event)
    )
    await asyncio.wait_for(reading.wait(), 1.0)
    await asyncio.sleep(0)  # que todas las hijas lleguen a su primer await
    assert len(cancel_event._waiters) == 1, "cancel_event se espera en más de un sitio"
    t.cancel()
    await asyncio.wait([t])


@pytest.mark.asyncio
async def test_wake_word_en_responding_cancela_el_turno() -> None:
    bus = EventBus()
    gateway, reading = _blocking_gateway()
    playback = MagicMock(spec=PlaybackEngine)
    t = asyncio.create_task(state_machine._responding(bus, gateway, playback, asyncio.Event()))
    await asyncio.wait_for(reading.wait(), 1.0)
    await asyncio.sleep(0)
    bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))
    with pytest.raises(state_machine._TurnCancelled):
        await asyncio.wait_for(t, 1.0)
    gateway.send_cancel.assert_awaited_once()
    playback.reset.assert_called()
    playback.drain.assert_not_awaited()
    assert bus._queues == [], "la suscripción de la wake word quedó registrada"
