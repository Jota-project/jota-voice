"""Tests para client.backends.registry."""
from __future__ import annotations

import pytest

from config import Config, GatewayConfig, AudioConfig, DisplayConfig, OWWConfig, DeviceConfig
from backends.errors import ConfigError
from backends import registry


def _cfg(audio_backend: str | None = None, display_backend: str | None = None) -> Config:
    return Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="x"),
        device=DeviceConfig(id="test"),
        audio=AudioConfig(backend=audio_backend),
        display=DisplayConfig(backend=display_backend),
    )


def test_make_audio_unsupported_os(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "is_termux", lambda: False)
    monkeypatch.setattr(registry.sys, "platform", "win32")
    with pytest.raises(ConfigError, match="SO no soportado"):
        registry.make_audio(_cfg())


def test_make_audio_unknown_backend() -> None:
    with pytest.raises(ConfigError, match="audio backend desconocido"):
        registry.make_audio(_cfg(audio_backend="alsa"))


def test_make_audio_termux_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry.sys, "platform", "darwin")
    inst = registry.make_audio(_cfg(audio_backend="termux"))
    assert inst.__class__.__name__ == "TermuxBackend"


def test_make_audio_sounddevice_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry.sys, "platform", "linux")
    inst = registry.make_audio(_cfg(audio_backend="sounddevice"))
    assert inst.__class__.__name__ == "SounddeviceBackend"


def test_make_audio_default_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "is_termux", lambda: False)
    monkeypatch.setattr(registry.sys, "platform", "darwin")
    inst = registry.make_audio(_cfg())
    assert inst.__class__.__name__ == "SounddeviceBackend"


def test_make_audio_default_termux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "is_termux", lambda: True)
    monkeypatch.setattr(registry.sys, "platform", "linux")
    inst = registry.make_audio(_cfg())
    assert inst.__class__.__name__ == "TermuxBackend"


def test_make_display_null_when_no_url_and_no_backend() -> None:
    cfg = Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="x"),
        device=DeviceConfig(id="test"),
        display=DisplayConfig(url="", backend=None),
    )
    inst = registry.make_display(cfg)
    assert inst.__class__.__name__ == "NullDisplayBackend"


def test_make_display_http_when_url_set() -> None:
    cfg = Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="x"),
        device=DeviceConfig(id="test"),
        display=DisplayConfig(url="http://127.0.0.1:8766", backend=None),
    )
    inst = registry.make_display(cfg)
    assert inst.__class__.__name__ == "HttpDisplayBackend"


def test_make_display_null_override() -> None:
    inst = registry.make_display(_cfg(display_backend="null"))
    assert inst.__class__.__name__ == "NullDisplayBackend"


def test_make_display_unknown_backend() -> None:
    with pytest.raises(ConfigError, match="display backend desconocido"):
        registry.make_display(_cfg(display_backend="mqtt"))


def test_make_oww_wyoming_default() -> None:
    inst = registry.make_oww(_cfg(), on_wake_word=None)
    assert inst.__class__.__name__ == "WyomingBackend"


def test_make_oww_unknown_backend() -> None:
    cfg = Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="x"),
        device=DeviceConfig(id="test"),
        oww=OWWConfig(backend="mqtt"),
    )
    with pytest.raises(ConfigError, match="oww backend desconocido"):
        registry.make_oww(cfg, on_wake_word=None)
