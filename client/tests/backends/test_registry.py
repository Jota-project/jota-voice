"""Tests para client.backends.registry."""
from __future__ import annotations

import pytest

from config import Config, GatewayConfig, AudioConfig, DisplayConfig, OWWConfig, DeviceConfig, MenubarConfig
from backends.errors import ConfigError
from backends import registry
from core.platform_key import sys as platform_key_sys


def _cfg(audio_backend: str | None = None, display_backend: str | None = None) -> Config:
    return Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="x"),
        device=DeviceConfig(id="test"),
        audio=AudioConfig(backend=audio_backend),
        display=DisplayConfig(backend=display_backend),
    )


def test_make_audio_unsupported_os(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PREFIX", raising=False)
    monkeypatch.setattr(platform_key_sys, "platform", "win32")
    with pytest.raises(ConfigError, match="SO no soportado"):
        registry.make_audio(_cfg())


def test_make_audio_unsupported_os_no_enumerated_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Para un sys.platform fuera de {darwin, linux*, win*} y sin PREFIX
    (detect_platform() lanza UnsupportedPlatformError), registry debe
    envolverlo en ConfigError para no romper el contrato de excepciones
    del módulo (cualquier SO no soportado -> ConfigError('SO no soportado...'))."""
    monkeypatch.delenv("PREFIX", raising=False)
    monkeypatch.setattr(platform_key_sys, "platform", "freebsd13")
    with pytest.raises(ConfigError, match="SO no soportado"):
        registry.make_audio(_cfg())


def test_make_audio_unknown_backend() -> None:
    with pytest.raises(ConfigError, match="audio backend desconocido"):
        registry.make_audio(_cfg(audio_backend="alsa"))


def test_make_audio_termux_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_key_sys, "platform", "darwin")
    inst = registry.make_audio(_cfg(audio_backend="termux"))
    assert inst.__class__.__name__ == "TermuxBackend"


def test_make_audio_sounddevice_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_key_sys, "platform", "linux")
    inst = registry.make_audio(_cfg(audio_backend="sounddevice"))
    assert inst.__class__.__name__ == "SounddeviceBackend"


def test_make_audio_default_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PREFIX", raising=False)
    monkeypatch.setattr(platform_key_sys, "platform", "darwin")
    inst = registry.make_audio(_cfg())
    assert inst.__class__.__name__ == "SounddeviceBackend"


def test_make_audio_default_termux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    monkeypatch.setattr(platform_key_sys, "platform", "linux")
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


def test_make_oww_propagate_audio_config_al_owwclient() -> None:
    """Issue #14: make_oww debe pasar cfg.audio al WyomingBackend, que a su
    vez lo propaga al OWWClient subyacente — eso es lo que el runtime usa
    realmente para declarar rate/channels en el protocolo Wyoming, en lugar
    de los hardcodeados 16000/mono. Verificamos el resultado observable
    (rate/channels que OWWClient verá al serializar audio-start/audio-chunk),
    no un campo de almacenamiento que podría descartarse sin afectar al
    comportamiento."""
    from backends.oww_wyoming import WyomingBackend

    cfg = Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="x"),
        device=DeviceConfig(id="test"),
        audio=AudioConfig(sample_rate=48000, channels=2),
    )
    inst = registry.make_oww(cfg, on_wake_word=None)
    assert isinstance(inst, WyomingBackend)
    # WyomingBackend.forwarda a OWWClient en __init__ — lo que el runtime usa
    # es inst._client._rate / inst._client._channels (los valores serializados
    # en los headers Wyoming). Si estos siguen siendo los defaults, el bug
    # #14 sigue ahí aunque la cadena de constructores parezca correcta.
    assert inst._client._rate == 48000, (
        f"OWWClient._rate esperaba 48000 (de cfg.audio), obtuve {inst._client._rate}"
    )
    assert inst._client._channels == 2, (
        f"OWWClient._channels esperaba 2 (de cfg.audio), obtuve {inst._client._channels}"
    )


def _cfg_with_menubar(enabled: bool = True) -> Config:
    return Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="x"),
        device=DeviceConfig(id="test"),
        menubar=MenubarConfig(enabled=enabled),
    )


def test_make_menubar_disabled_returns_null() -> None:
    inst = registry.make_menubar(_cfg_with_menubar(enabled=False))
    assert inst.__class__.__name__ == "NullMenubarBackend"


def test_make_menubar_linux_returns_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_key_sys, "platform", "linux")
    inst = registry.make_menubar(_cfg_with_menubar())
    assert inst.__class__.__name__ == "NullMenubarBackend"


def test_make_menubar_darwin_no_pyobjc_returns_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_key_sys, "platform", "darwin")

    orig_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "ui.menubar_cocoa" or name.endswith(".menubar_cocoa"):
            raise ImportError("no pyobjc")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    inst = registry.make_menubar(_cfg_with_menubar())
    assert inst.__class__.__name__ == "NullMenubarBackend"


def test_make_menubar_darwin_with_pyobjc_returns_cocoa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_key_sys, "platform", "darwin")
    import types
    import sys as _sys

    fake_cocoa = types.ModuleType("ui.menubar_cocoa")

    class FakeCocoaBackend:
        def __init__(self, cfg):
            self.cfg = cfg

    fake_cocoa.CocoaMenubarBackend = FakeCocoaBackend
    monkeypatch.setitem(_sys.modules, "ui.menubar_cocoa", fake_cocoa)
    inst = registry.make_menubar(_cfg_with_menubar())
    assert isinstance(inst, FakeCocoaBackend)
