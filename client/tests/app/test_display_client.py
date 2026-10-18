"""test_display_client.py — mapeo VoiceEvent -> mensajes del protocolo display.

Sin red: un backend falso captura las llamadas a send().
"""
from __future__ import annotations

import asyncio

import pytest

from app.display_client import DEGRADED_TTL_S, DisplayClient, health_from_gateway_status
from domain.event_bus import EventBus, VoiceEvent

from tests.fakes.waiting import wait_until


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def send(self, type: str, data: dict) -> None:
        self.calls.append((type, data))


def _run(events: list[VoiceEvent]) -> list[tuple[str, dict]]:
    backend = FakeBackend()
    client = DisplayClient(backend)
    for ev in events:
        client._handle(ev)
    return backend.calls


def test_mapeo_del_turno_completo() -> None:
    calls = _run([
        VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_jota"}),
        VoiceEvent(type="recording_started", data={}),
        VoiceEvent(type="recording_ended", data={}),
        VoiceEvent(type="transcription_partial", data={"text": "ho"}),
        VoiceEvent(type="transcription", data={"text": "hola jota"}),
        VoiceEvent(type="turn_start", data={"turn_id": "t-1", "turn_seq": 1}),
        VoiceEvent(type="llm_token", data={"content": "Buenos"}),
        VoiceEvent(type="playback_started", data={}),
        VoiceEvent(type="display_text_update", data={
            "chunk_text": "Buenos días", "cumulative_text": "Buenos días", "duration_s": 0.5,
        }),
        VoiceEvent(type="playback_ended", data={}),
        VoiceEvent(type="state_changed", data={"state": "idle"}),
    ])
    assert calls == [
        ("state", {"value": "listening", "text": ""}),
        ("state", {"value": "thinking", "text": "hola jota"}),
        ("turn_start", {"turn_id": "t-1", "turn_seq": 1}),
        ("state", {"value": "response", "text": ""}),
        ("text_update", {
            "chunk_text": "Buenos días", "cumulative_text": "Buenos días", "duration_s": 0.5,
        }),
        ("state", {"value": "idle", "text": ""}),
    ]


def test_state_changed_que_no_es_idle_se_ignora() -> None:
    assert _run([
        VoiceEvent(type="state_changed", data={"from": "IDLE", "to": "RECORDING"}),
        VoiceEvent(type="state_changed", data={"state": "recording"}),
    ]) == []


def test_tool_call_con_campos_del_gateway() -> None:
    """Campos de jota-gateway docs/client-protocol.md §5 (phase, no status)."""
    calls = _run([VoiceEvent(type="tool_call", data={
        "type": "tool_call", "turn_id": "t-1", "phase": "result", "name": "exec",
        "tool_call_id": "call-1", "args": None, "result": "file.txt", "is_error": False,
    })])
    assert calls == [("tool_call", {
        "phase": "result", "name": "exec", "tool_call_id": "call-1",
        "args": None, "result": "file.txt", "is_error": False,
    })]


def test_error_y_cancelled_llegan_al_display() -> None:
    """Issue #91: antes no había rama para error/cancelled."""
    calls = _run([
        VoiceEvent(type="error", data={"code": "TURN_ERROR", "message": "boom", "fatal": False}),
        VoiceEvent(type="error", data={"message": "Timeout en estado RESPONDING"}),
        VoiceEvent(type="cancelled", data={"state": "RESPONDING"}),
    ])
    assert calls == [
        ("error", {"code": "TURN_ERROR", "message": "boom", "fatal": False}),
        ("error", {"code": None, "message": "Timeout en estado RESPONDING", "fatal": False}),
        ("cancelled", {"state": "RESPONDING"}),
    ]


def test_display_client_no_guarda_el_bus() -> None:
    """Issue #91: el atributo _bus era código muerto."""
    assert not hasattr(DisplayClient(FakeBackend()), "_bus")


# ---------------------------------------------------------------------------
# health (parte display de #7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state,level,ttl", [
    ("unavailable", "warning", None),
    ("reconnecting", "warning", None),
    ("restored", "ok", None),
    ("degraded", "info", DEGRADED_TTL_S),
    ("algo_nuevo", "info", DEGRADED_TTL_S),
])
def test_health_de_servicios_del_gateway(state: str, level: str, ttl) -> None:
    msg = health_from_gateway_status({"service": "transcriber", "state": state})
    assert msg["key"] == "gateway.transcriber"
    assert msg["level"] == level
    assert msg["ttl_s"] == ttl
    assert msg["service"] == "transcriber" and msg["state"] == state


def test_transcriber_degraded_es_aviso_que_caduca_no_error() -> None:
    """jota-gateway#227: `transcriber degraded` falsos en reposo."""
    msg = health_from_gateway_status({"service": "transcriber", "state": "degraded"})
    assert msg["level"] == "info"
    assert msg["ttl_s"] and msg["ttl_s"] > 0


def test_health_de_la_sesion_local() -> None:
    """gateway_session publica service=gateway con disconnected/reconnecting/connected."""
    down = health_from_gateway_status({"service": "gateway", "state": "disconnected",
                                       "code": 1006, "reason": ""})
    up = health_from_gateway_status({"service": "gateway", "state": "connected",
                                     "session_id": "s"})
    assert down["key"] == up["key"] == "gateway"
    assert down["level"] == "warning" and down["detail"] == {"code": 1006, "reason": ""}
    assert up["level"] == "ok"


def test_oww_degraded_y_recovered() -> None:
    calls = _run([
        VoiceEvent(type="gateway_status", data={"service": "tts", "state": "unavailable"}),
        VoiceEvent(type="oww_degraded", data={"consecutive_failures": 3, "backoff_s": 20}),
        VoiceEvent(type="oww_recovered", data={}),
    ])
    assert [c[0] for c in calls] == ["health", "health", "health"]
    assert calls[0][1]["key"] == "gateway.tts"
    assert calls[1][1] == {"key": "oww", "level": "warning", "service": "oww",
                           "state": "degraded", "ttl_s": None,
                           "detail": {"consecutive_failures": 3, "backoff_s": 20}}
    assert calls[2][1]["level"] == "ok" and calls[2][1]["key"] == "oww"


# ---------------------------------------------------------------------------
# Bucle sobre el bus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_que_falla_no_tumba_el_bucle() -> None:
    class Boom(FakeBackend):
        def send(self, type: str, data: dict) -> None:
            super().send(type, data)
            if type == "state" and data["value"] == "listening":
                raise RuntimeError("backend roto")

    bus = EventBus()
    backend = Boom()
    task = asyncio.create_task(DisplayClient(backend).run(bus))
    try:
        await asyncio.sleep(0)
        bus.publish(VoiceEvent(type="recording_started", data={}))
        bus.publish(VoiceEvent(type="state_changed", data={"state": "idle"}))
        await wait_until(lambda: len(backend.calls) == 2, message="sigue vivo")
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_rafaga_de_text_updates_no_deja_backlog_en_el_bus() -> None:
    """Issue #64: send() no bloquea, así que la cola del suscriptor se drena
    al ritmo de publicación aunque el display sea lento."""
    bus = EventBus()
    backend = FakeBackend()
    task = asyncio.create_task(DisplayClient(backend).run(bus))
    try:
        await asyncio.sleep(0)
        for i in range(1000):
            bus.publish(VoiceEvent(type="display_text_update", data={
                "chunk_text": "x", "cumulative_text": "x" * (i + 1), "duration_s": 0.01,
            }))
        await wait_until(lambda: len(backend.calls) == 1000, message="drenado")
        assert all(q.qsize() == 0 for q in bus._queues)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
