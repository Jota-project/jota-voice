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


def test_termux_via_termux_hosts_path_when_prefix_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback de Fase A revisión: si PREFIX no está en el entorno (p.ej.
    init system que sanitiza env vars), seguimos detectando Termux por la
    existencia de TERMUX_HOSTS_PATH, igual que el is_termux() pre-Fase-A.
    No monkeypatch del filesystem (la constante TERMUX_HOSTS_PATH se
    reescribe a un path inexistente en tests, pero esto verifica que
    cuando el path SÍ existe, detecta Termux)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        # Hack: usar el tmpdir como "TERMUX_HOSTS_PATH" via monkeypatch
        # del símbolo importado en core.platform_key — pero
        # detect_platform() ya importa la constante vía backends.platform_detect
        # al import. Lo más limpio es hacer que el path exista REALMENTE.
        hosts_file = f"{tmp}/hosts"
        open(hosts_file, "w").close()
        monkeypatch.setattr("core.platform_key.TERMUX_HOSTS_PATH", hosts_file)
        monkeypatch.setattr("core.platform_key.sys.platform", "linux")
        monkeypatch.delenv("PREFIX", raising=False)
        assert detect_platform() == PlatformKey("termux", "mobile")


def test_termux_fallback_not_used_when_hosts_path_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin PREFIX y sin TERMUX_HOSTS_PATH, sigue cayendo a linux desktop
    (no a termux) — el fallback solo se activa si el path existe."""
    monkeypatch.setattr(
        "core.platform_key.TERMUX_HOSTS_PATH",
        "/this/path/does/not/exist/and/should/never/be/created",
    )
    monkeypatch.setattr("core.platform_key.sys.platform", "linux")
    monkeypatch.delenv("PREFIX", raising=False)
    assert detect_platform() == PlatformKey("linux", "desktop")
