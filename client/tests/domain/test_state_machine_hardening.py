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
from backends.gateway_client import GatewayClient, GatewayDisconnected
from config import Config, DeviceConfig, GatewayConfig, OWWConfig
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
