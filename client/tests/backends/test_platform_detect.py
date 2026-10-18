"""Tests del helper único de detección de Termux."""
from __future__ import annotations

import pytest

from backends import platform_detect


@pytest.fixture(autouse=True)
def _sin_termux_version(monkeypatch: pytest.MonkeyPatch) -> None:
    # El entorno real (CI en Termux, por ejemplo) no debe decidir los tests.
    monkeypatch.delenv("TERMUX_VERSION", raising=False)


def test_is_termux_true_when_hosts_file_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_detect.os.path, "exists", lambda p: True)
    assert platform_detect.is_termux() is True


def test_is_termux_false_when_hosts_file_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_detect.os.path, "exists", lambda p: False)
    assert platform_detect.is_termux() is False


def test_is_termux_usa_termux_version_si_falta_el_fichero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #60: una variante de Termux sin ese path concreto se seguía
    detectando como Linux genérico."""
    monkeypatch.setattr(platform_detect.os.path, "exists", lambda p: False)
    monkeypatch.setenv("TERMUX_VERSION", "0.118.1")
    assert platform_detect.is_termux() is True


def test_is_termux_ignora_termux_version_vacia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_detect.os.path, "exists", lambda p: False)
    monkeypatch.setenv("TERMUX_VERSION", "")
    assert platform_detect.is_termux() is False


def test_is_termux_tambien_reconoce_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mismas señales que core.platform_key.detect_platform (no deben divergir)."""
    monkeypatch.setattr(platform_detect.os.path, "exists", lambda p: False)
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    assert platform_detect.is_termux() is True


def test_detect_platform_usa_termux_version_como_senal(monkeypatch: pytest.MonkeyPatch) -> None:
    """registry.py elige el backend de audio con detect_platform(): sin PREFIX
    (init que sanea el entorno) ni el fichero de hosts, TERMUX_VERSION evita
    caer en "linux" y elegir sounddevice, que en Termux graba silencio."""
    from core import platform_key

    monkeypatch.setattr(platform_key.sys, "platform", "linux")
    monkeypatch.delenv("PREFIX", raising=False)
    monkeypatch.setattr(platform_key.os.path, "exists", lambda p: False)
    assert platform_key.detect_platform().family == "linux"
    monkeypatch.setenv("TERMUX_VERSION", "0.118.1")
    assert platform_key.detect_platform().family == "termux"
