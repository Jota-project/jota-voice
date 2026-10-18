"""RESPONDING -> eventos para el display (Fase D): turn_start y tool_call
solo del turno propio (el que abre el gateway tras nuestro `send`)."""
from __future__ import annotations

import pytest

from tests.domain.test_gateway_session import _ev, _respond


@pytest.fixture(autouse=True)
def _short_grace(monkeypatch):
    from domain import state_machine
    monkeypatch.setattr(state_machine, "TURN_END_GRACE_S", 0.05, raising=False)


@pytest.mark.asyncio
async def test_turn_start_propio_se_publica_y_el_ajeno_no():
    _, _, got = await _respond([
        _ev("turn_start", turn_id="t-3", turn_seq=3),        # push del agente
        _ev("transcription", text="hola"),
        _ev("turn_start", turn_id="t-4", turn_seq=4),        # el nuestro
        _ev("tts_chunk", audio=b"\x01\x00", turn_seq=4),
        _ev("turn_end", turn_id="t-4"),
    ])
    starts = [e.data for e in got if e.type == "turn_start"]
    assert starts == [{"turn_id": "t-4", "turn_seq": 4}]


@pytest.mark.asyncio
async def test_tool_call_propio_se_publica_con_los_campos_del_gateway():
    tool = dict(turn_id="t-4", phase="start", name="exec", tool_call_id="c-1",
                args={"command": "ls"}, result=None, is_error=None)
    _, _, got = await _respond([
        _ev("tool_call", **{**tool, "turn_id": "t-3", "tool_call_id": "ajeno"}),
        _ev("transcription", text="hola"),
        _ev("turn_start", turn_id="t-4", turn_seq=4),
        _ev("tool_call", **tool),
        _ev("tts_chunk", audio=b"\x01\x00", turn_seq=4),
        _ev("turn_end", turn_id="t-4"),
    ])
    calls = [e.data for e in got if e.type == "tool_call"]
    assert calls == [tool]


@pytest.mark.asyncio
async def test_turn_start_va_antes_que_el_texto_del_turno():
    """El display correlaciona por turn_id: el turn_start debe llegarle antes
    que el primer text_update de ese turno."""
    _, _, got = await _respond([
        _ev("transcription", text="hola"),
        _ev("turn_start", turn_id="t-1", turn_seq=1),
        _ev("llm_token", turn_id="t-1", content="Buenas"),
        _ev("tts_chunk", audio=b"\x01\x00", turn_seq=1),
        _ev("turn_end", turn_id="t-1"),
    ])
    types = [e.type for e in got]
    assert types.index("turn_start") < types.index("playback_started")
