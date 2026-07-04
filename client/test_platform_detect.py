"""Tests del helper único de detección de Termux."""
from __future__ import annotations

import pytest

from backends import platform_detect


def test_is_termux_true_when_hosts_file_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_detect.os.path, "exists", lambda p: True)
    assert platform_detect.is_termux() is True


def test_is_termux_false_when_hosts_file_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_detect.os.path, "exists", lambda p: False)
    assert platform_detect.is_termux() is False
