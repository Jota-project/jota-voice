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


def _display_cfg(**kw) -> Config:
    return Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="x"),
        device=DeviceConfig(id="test"),
        display=DisplayConfig(**kw),
    )


@pytest.mark.parametrize("plat", ["darwin", "linux"])
def test_make_display_ws_por_defecto_en_macos_y_linux(monkeypatch: pytest.MonkeyPatch, plat: str) -> None:
    monkeypatch.delenv("PREFIX", raising=False)
    monkeypatch.setattr(platform_key_sys, "platform", plat)
    inst = registry.make_display(_display_cfg())
    assert inst.__class__.__name__ == "WsDisplayBackend"


def test_make_display_http_por_defecto_en_termux(monkeypatch: pytest.MonkeyPatch) -> None:
    """El kiosk de jota-display vive en Termux y solo habla POST /state."""
    from config import DEFAULT_DISPLAY_HTTP_URL
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    monkeypatch.setattr(platform_key_sys, "platform", "linux")
    inst = registry.make_display(_display_cfg())
    assert inst.__class__.__name__ == "HttpDisplayBackend"
    assert inst._url == DEFAULT_DISPLAY_HTTP_URL + "/state"


def test_make_display_http_when_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compat: configs desplegadas (copiadas del config.example.yaml antiguo)
    tienen `backend: null` + `url:`. Una url explícita sigue eligiendo http
    en cualquier plataforma: el kiosk del teléfono no cambia."""
    monkeypatch.delenv("PREFIX", raising=False)
    monkeypatch.setattr(platform_key_sys, "platform", "darwin")
    inst = registry.make_display(_display_cfg(url="http://127.0.0.1:8766", backend=None))
    assert inst.__class__.__name__ == "HttpDisplayBackend"


def test_make_display_http_explicito_sin_url_usa_default_unico() -> None:
    """Issue #58: `backend: http` sin url ya no construye la URL relativa
    "/state"; usa la misma constante que el resto del código."""
    from config import DEFAULT_DISPLAY_HTTP_URL
    inst = registry.make_display(_display_cfg(backend="http"))
    assert inst._url == DEFAULT_DISPLAY_HTTP_URL + "/state"


def test_make_display_http_url_invalida_falla_al_arrancar() -> None:
    """Issue #58: una url sin esquema/host falla al construir (ConfigError),
    no en cada update() silenciada a DEBUG."""
    with pytest.raises(ConfigError, match="display.url"):
        registry.make_display(_display_cfg(backend="http", url="127.0.0.1:8766"))


def test_make_display_ws_recibe_on_cancel() -> None:
    called: list[bool] = []
    inst = registry.make_display(_display_cfg(backend="ws"), on_cancel=lambda: called.append(True))
    inst._on_cancel()
    assert called == [True]


def test_make_display_null_override() -> None:
    inst = registry.make_display(_cfg(display_backend="null"))
    assert inst.__class__.__name__ == "NullDisplayBackend"


def test_make_display_unknown_backend() -> None:
    with pytest.raises(ConfigError, match="display backend desconocido"):
        registry.make_display(_cfg(display_backend="mqtt"))


def test_make_oww_wyoming_default() -> None:
    inst = registry.make_oww(_cfg())
    assert inst.__class__.__name__ == "WyomingBackend"


def test_make_oww_unknown_backend() -> None:
    cfg = Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="x"),
        device=DeviceConfig(id="test"),
        oww=OWWConfig(backend="mqtt"),
    )
    with pytest.raises(ConfigError, match="oww backend desconocido"):
        registry.make_oww(cfg)


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
    inst = registry.make_oww(cfg)
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


def test_wyoming_run_forever_reenvia_el_callback_al_owwclient(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #90: el callback solo entra por run_forever (ya no hay rama de
    'callback del constructor' validada en runtime) y llega tal cual a OWWClient."""
    import asyncio

    inst = registry.make_oww(_cfg())
    seen: list = []

    async def _fake_run_forever(audio, cb) -> None:
        seen.append((audio, cb))

    monkeypatch.setattr(inst._client, "run_forever", _fake_run_forever)

    async def _on_wake(name: str) -> None:
        pass

    audio = object()
    asyncio.run(inst.run_forever(audio, _on_wake))
    assert seen == [(audio, _on_wake)]


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


def test_make_menubar_darwin_cocoa_init_fails_returns_null(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #32: si CocoaMenubarBackend.__init__ lanza una excepción distinta
    de ImportError (p.ej. sin sesión Aqua/WindowServer), make_menubar debe
    caer a NullMenubarBackend en lugar de propagar la excepción."""
    monkeypatch.setattr(platform_key_sys, "platform", "darwin")
    import types
    import sys as _sys

    fake_cocoa = types.ModuleType("ui.menubar_cocoa")

    class FailingCocoaBackend:
        def __init__(self, cfg):
            raise RuntimeError("NSApp not running (no Aqua session)")

    fake_cocoa.CocoaMenubarBackend = FailingCocoaBackend
    monkeypatch.setitem(_sys.modules, "ui.menubar_cocoa", fake_cocoa)
    inst = registry.make_menubar(_cfg_with_menubar())
    assert inst.__class__.__name__ == "NullMenubarBackend"
