"""Tests for the NullMenubarBackend (used on non-macOS and in tests)."""
from __future__ import annotations

import logging

from ui.menubar_base import MenubarBackend, MenubarCommands
from ui.menubar_null import NullMenubarBackend


def test_null_backend_satisfies_protocol():
    assert isinstance(NullMenubarBackend(), MenubarBackend)


def test_null_methods_return_none(caplog):
    b = NullMenubarBackend()
    with caplog.at_level(logging.DEBUG, logger="ui.menubar_null"):
        assert b.set_state("listening") is None
        assert b.set_status_text("hola") is None
        assert b.set_listening_paused(True) is None
        assert b.set_errors_count(3) is None
        cmds = MenubarCommands(
            on_toggle_pause=lambda: None,
            on_open_logs=lambda: None,
            on_open_config=lambda: None,
            on_shutdown_service=lambda: None,
            on_quit=lambda: None,
        )
        assert b.set_commands(cmds) is None
