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


def test_wake_word_detected_sets_listening_instantly():
    """El icono debe reaccionar en el instante en que se detecta la wake
    word, no esperar a un state_changed (que la state machine actual NO
    emite para "listening" — solo para "idle")."""
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        task = asyncio.create_task(client.run(bus, ui_queue, pause))
        await asyncio.sleep(0.05)
        bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "hey_jarvis"}))
        await asyncio.sleep(0.05)
        bus.close()
        await asyncio.wait_for(task, timeout=2.0)

    _run(scenario())

    set_state_vals = [c[1][0] for c in backend.calls if c[0] == "set_state"]
    assert set_state_vals == ["listening"]


def test_recording_ended_sets_thinking():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        task = asyncio.create_task(client.run(bus, ui_queue, pause))
        await asyncio.sleep(0.05)
        bus.publish(VoiceEvent(type="recording_ended", data={}))
        await asyncio.sleep(0.05)
        bus.close()
        await asyncio.wait_for(task, timeout=2.0)

    _run(scenario())

    set_state_vals = [c[1][0] for c in backend.calls if c[0] == "set_state"]
    assert set_state_vals == ["thinking"]


def test_playback_started_sets_speaking():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        task = asyncio.create_task(client.run(bus, ui_queue, pause))
        await asyncio.sleep(0.05)
        bus.publish(VoiceEvent(type="playback_started", data={}))
        await asyncio.sleep(0.05)
        bus.close()
        await asyncio.wait_for(task, timeout=2.0)

    _run(scenario())

    set_state_vals = [c[1][0] for c in backend.calls if c[0] == "set_state"]
    assert set_state_vals == ["speaking"]


def test_error_also_sets_error_state():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        task = asyncio.create_task(client.run(bus, ui_queue, pause))
        await asyncio.sleep(0.05)
        bus.publish(VoiceEvent(type="error", data={"message": "boom"}))
        await asyncio.sleep(0.05)
        bus.close()
        await asyncio.wait_for(task, timeout=2.0)

    _run(scenario())

    set_state_vals = [c[1][0] for c in backend.calls if c[0] == "set_state"]
    assert set_state_vals == ["error"]


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


def test_cancelled_event_sets_cancelled_state():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        task = asyncio.create_task(client.run(bus, ui_queue, pause))
        await asyncio.sleep(0.05)
        bus.publish(VoiceEvent(type="cancelled", data={}))
        await asyncio.sleep(0.05)
        bus.close()
        await asyncio.wait_for(task, timeout=2.0)

    _run(scenario())

    set_state_vals = [c[1][0] for c in backend.calls if c[0] == "set_state"]
    assert set_state_vals == ["cancelled"]


def test_error_state_holds_before_reverting_to_idle():
    """Sin hold, state_changed("idle") (publicado por _idle() en la siguiente
    iteración del loop) sobreescribe el icono de error casi al instante,
    haciéndolo imperceptible. Con hold, "idle" debe demorarse hasta que
    pase la ventana de hold desde el último estado transitorio (error o
    cancelled)."""
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend, hold_transient_s=0.15)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        task = asyncio.create_task(client.run(bus, ui_queue, pause))
        await asyncio.sleep(0.02)
        bus.publish(VoiceEvent(type="error", data={"message": "boom"}))
        bus.publish(VoiceEvent(type="state_changed", data={"state": "idle"}))
        await asyncio.sleep(0.05)
        # Aún dentro de la ventana de hold: debe seguir mostrando "error".
        assert backend.calls[-1] == ("set_state", ("error",)), backend.calls
        await asyncio.sleep(0.2)
        # Pasada la ventana de hold: debe haber revertido a "idle".
        assert backend.calls[-1] == ("set_state", ("idle",)), backend.calls
        bus.close()
        await asyncio.wait_for(task, timeout=2.0)

    _run(scenario())


def test_new_real_event_during_hold_overrides_pending_idle():
    """Si llega un evento real (p.ej. una nueva wake word) mientras el
    icono de error/cancelled está retenido, debe mostrarse de inmediato —
    no debe quedar oculto esperando a que expire el hold ni a que el idle
    diferido se aplique por encima."""
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend, hold_transient_s=0.3)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        task = asyncio.create_task(client.run(bus, ui_queue, pause))
        await asyncio.sleep(0.02)
        bus.publish(VoiceEvent(type="error", data={"message": "boom"}))
        bus.publish(VoiceEvent(type="state_changed", data={"state": "idle"}))
        await asyncio.sleep(0.05)
        bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_jota"}))
        await asyncio.sleep(0.05)
        assert backend.calls[-1] == ("set_state", ("listening",)), backend.calls
        bus.close()
        await asyncio.wait_for(task, timeout=2.0)

    _run(scenario())


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
