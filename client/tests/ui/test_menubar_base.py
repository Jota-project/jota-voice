"""Tests for the MenubarBackend Protocol and _SharedState thread-safety."""
from __future__ import annotations

import threading

from ui.menubar_base import MenubarBackend, MenubarCommands, _SharedState


def test_shared_state_defaults():
    s = _SharedState()
    assert s.read() == ("idle", "", 0, False)


def test_shared_state_update_and_read():
    s = _SharedState()
    s.update(state="listening", last_text="hola", errors_count=2, listening_paused=True)
    assert s.read() == ("listening", "hola", 2, True)


def test_shared_state_partial_update():
    s = _SharedState()
    s.update(state="speaking")
    # other fields preserved
    assert s.read()[0] == "speaking"
    assert s.read()[1] == ""
    assert s.read()[2] == 0
    assert s.read()[3] is False


def test_shared_state_lock_no_deadlock():
    s = _SharedState()
    errors: list[Exception] = []

    def writer():
        try:
            for _ in range(1000):
                s.update(state="listening", errors_count=1)
                s.update(state="idle")
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            for _ in range(1000):
                _ = s.read()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
        assert not t.is_alive(), "thread deadlocked"
    assert not errors, f"thread errors: {errors}"


def test_menubar_commands_callables():
    called: list[str] = []

    def make(name: str):
        return lambda: called.append(name)

    cmds = MenubarCommands(
        on_toggle_pause=make("pause"),
        on_open_logs=make("logs"),
        on_open_config=make("config"),
        on_shutdown_service=make("shutdown"),
        on_quit=make("quit"),
    )
    cmds.on_toggle_pause()
    cmds.on_open_logs()
    cmds.on_open_config()
    cmds.on_shutdown_service()
    cmds.on_quit()
    assert called == ["pause", "logs", "config", "shutdown", "quit"]


def test_protocol_is_runtime_checkable():
    class Fake:
        def set_state(self, state: str) -> None: ...
        def set_status_text(self, text: str) -> None: ...
        def set_listening_paused(self, paused: bool) -> None: ...
        def set_errors_count(self, n: int) -> None: ...
        def set_commands(self, cmds) -> None: ...

    assert isinstance(Fake(), MenubarBackend)
