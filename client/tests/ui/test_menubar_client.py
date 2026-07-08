"""Tests offline de MenubarClient: mapeo de eventos y drenaje de cola."""
from __future__ import annotations

import asyncio
import queue

from domain.event_bus import EventBus, VoiceEvent
from ui.menubar_base import MenubarCommands
from ui.menubar_client import MenubarClient


class FakeBackend:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def set_state(self, state: str) -> None:
        self.calls.append(("set_state", (state,)))

    def set_status_text(self, text: str) -> None:
        self.calls.append(("set_status_text", (text,)))

    def set_listening_paused(self, paused: bool) -> None:
        self.calls.append(("set_listening_paused", (paused,)))

    def set_errors_count(self, n: int) -> None:
        self.calls.append(("set_errors_count", (n,)))

    def set_commands(self, cmds: MenubarCommands) -> None:
        self.calls.append(("set_commands", (id(cmds),)))


def _run(coro):
    return asyncio.run(coro)


def test_state_changed_propagates():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        task = asyncio.create_task(client.run(bus, ui_queue, pause))
        await asyncio.sleep(0.05)  # let subscription register
        bus.publish(VoiceEvent(type="state_changed", data={"state": "listening"}))
        bus.publish(VoiceEvent(type="transcription", data={"text": "hola"}))
        await asyncio.sleep(0.05)
        bus.close()
        await asyncio.wait_for(task, timeout=2.0)

    _run(scenario())

    methods = [c[0] for c in backend.calls]
    assert "set_state" in methods
    assert "set_status_text" in methods
    set_state_vals = [c[1][0] for c in backend.calls if c[0] == "set_state"]
    assert "listening" in set_state_vals
    set_text_vals = [c[1][0] for c in backend.calls if c[0] == "set_status_text"]
    assert "hola" in set_text_vals


def test_error_increments_count():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        task = asyncio.create_task(client.run(bus, ui_queue, pause))
        await asyncio.sleep(0.05)
        bus.publish(VoiceEvent(type="error", data={"message": "boom"}))
        bus.publish(VoiceEvent(type="error", data={"message": "boom2"}))
        await asyncio.sleep(0.05)
        bus.close()
        await asyncio.wait_for(task, timeout=2.0)

    _run(scenario())
    err_vals = sorted(c[1][0] for c in backend.calls if c[0] == "set_errors_count")
    assert err_vals == [1, 2]


def test_toggle_pause_flips_event():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()
    # simulate UI thread pushing commands
    ui_queue.put_nowait("toggle_pause")
    ui_queue.put_nowait("toggle_pause")

    async def scenario():
        task = asyncio.create_task(client.run(bus, ui_queue, pause))
        # give it time to drain two toggle_pause and a third to confirm state
        ui_queue.put_nowait("toggle_pause")
        await asyncio.sleep(0.1)
        bus.close()
        await asyncio.wait_for(task, timeout=2.0)

    _run(scenario())

    # After three toggles: set, clear, set -> paused=True
    assert pause.is_set()
    paused_calls = [c[1][0] for c in backend.calls if c[0] == "set_listening_paused"]
    assert paused_calls == [True, False, True]


def test_run_returns_on_bus_close():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        task = asyncio.create_task(client.run(bus, ui_queue, pause))
        await asyncio.sleep(0.05)
        bus.close()
        await asyncio.wait_for(task, timeout=2.0)
        return task.result()

    _run(scenario())
