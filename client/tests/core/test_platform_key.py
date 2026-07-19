import pytest

from core.platform_key import PlatformKey, UnsupportedPlatformError, detect_platform


def test_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.platform_key.sys.platform", "darwin")
    assert detect_platform() == PlatformKey("darwin", "desktop")


def test_termux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.platform_key.sys.platform", "linux")
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    assert detect_platform() == PlatformKey("termux", "mobile")


def test_linux_desktop_without_termux_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.platform_key.sys.platform", "linux")
    monkeypatch.delenv("PREFIX", raising=False)
    assert detect_platform() == PlatformKey("linux", "desktop")


def test_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.platform_key.sys.platform", "win32")
    assert detect_platform() == PlatformKey("windows", "desktop")


def test_unsupported_platform_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.platform_key.sys.platform", "freebsd13")
    monkeypatch.delenv("PREFIX", raising=False)
    with pytest.raises(UnsupportedPlatformError):
        detect_platform()
