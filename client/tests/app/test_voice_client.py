"""Tests focalizados de ciclo de vida en voice_client.

Cubre que los fallos de audio.start() queden visibles en el menubar y apaguen
limpiamente, que el hilo de asyncio no muera en silencio ni deje handlers de
señal inválidos, y que el run loop Cocoa se detenga tras el cleanup asíncrono.
"""
from __future__ import annotations

import asyncio
import sys

import pytest

from app import voice_client
from backends import registry

# Los tests que invocan _run_with_cocoa_menubar hacen AppKit/Foundation
# en runtime (signal.set_wakeup_fd + NSFileHandle watcher, fix de #20).
# En Linux/Termux, AppKit no existe y el import lazy fallaría — solo se
# puede ejercitar este path en macOS.
pytestmark_cocoa = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Cocoa runloop solo instalable con AppKit (macOS)",
)
from config import Config, GatewayConfig


class _SpyMenubarBackend:
    def __init__(self) -> None:
        self.states: list[str] = []
        self.status_texts: list[str] = []
        self.commands = None
        self.run_forever_called = False

    def set_state(self, state: str) -> None:
        self.states.append(state)

    def set_status_text(self, text: str) -> None:
        self.status_texts.append(text)

    def set_listening_paused(self, paused: bool) -> None:
        pass

    def set_errors_count(self, n: int) -> None:
        pass

    def set_commands(self, cmds) -> None:
        self.commands = cmds

    def run_forever(self) -> None:
        self.run_forever_called = True


class _FailingAudioBackend:
    async def start(self) -> None:
        raise RuntimeError("permiso de micrófono denegado")

    async def stop(self) -> None:
        pass


def _make_cfg() -> Config:
    return Config(gateway=GatewayConfig(client_key="test", host="localhost"))


def test_call_soon_threadsafe_safe_ignores_closed_loop() -> None:
    loop = asyncio.new_event_loop()
    stop_event = asyncio.Event()
    loop.close()

    voice_client._call_soon_threadsafe_safe(loop, stop_event)  # no debe lanzar


def test_call_soon_threadsafe_safe_sets_event_on_open_loop() -> None:
    loop = asyncio.new_event_loop()
    stop_event = asyncio.Event()

    voice_client._call_soon_threadsafe_safe(loop, stop_event)
    loop.run_until_complete(asyncio.sleep(0))
    loop.close()

    assert stop_event.is_set()


async def _test_audio_start_failure_is_visible_and_shuts_down_cleanly(monkeypatch) -> None:
    monkeypatch.setattr(registry, "make_audio", lambda cfg: _FailingAudioBackend())

    menubar = _SpyMenubarBackend()
    cfg = _make_cfg()

    # No debe propagar la excepción de audio.start() fuera de main().
    await voice_client.main(cfg, menubar, external_stop_event=asyncio.Event())

    assert "error" in menubar.states
    assert menubar.status_texts, "el usuario debe ver un texto descriptivo del fallo"
    # El pipeline no debe seguir montándose sobre un backend de audio muerto.
    assert menubar.commands is None


def test_audio_start_failure_is_visible_and_shuts_down_cleanly(monkeypatch) -> None:
    asyncio.run(_test_audio_start_failure_is_visible_and_shuts_down_cleanly(monkeypatch))


class _ExplodingMenubarBackend(_SpyMenubarBackend):
    def set_state(self, state: str) -> None:
        raise RuntimeError("menubar backend roto")


async def _test_audio_start_failure_does_not_propagate_if_menubar_reporting_fails(monkeypatch) -> None:
    monkeypatch.setattr(registry, "make_audio", lambda cfg: _FailingAudioBackend())

    menubar = _ExplodingMenubarBackend()
    cfg = _make_cfg()

    # La excepción original de audio.start() no debe perderse su manejo
    # por un fallo secundario al reportarla al menubar.
    await voice_client.main(cfg, menubar, external_stop_event=asyncio.Event())


def test_audio_start_failure_does_not_propagate_if_menubar_reporting_fails(monkeypatch) -> None:
    asyncio.run(_test_audio_start_failure_does_not_propagate_if_menubar_reporting_fails(monkeypatch))


@pytestmark_cocoa
def test_run_with_cocoa_menubar_survives_early_main_failure(monkeypatch) -> None:
    async def _fake_main(cfg, menubar_backend, *, external_stop_event=None):
        raise RuntimeError("fallo antes de construir nada")

    monkeypatch.setattr(voice_client, "main", _fake_main)

    signal_calls: list[tuple] = []
    monkeypatch.setattr(
        voice_client.signal, "signal",
        lambda sig, handler: signal_calls.append((sig, handler)),
    )

    menubar = _SpyMenubarBackend()
    cfg = _make_cfg()

    voice_client._run_with_cocoa_menubar(cfg, menubar)

    assert menubar.run_forever_called, "la app debe seguir mostrando el icono aunque main() falle"
    assert "error" in menubar.states


@pytestmark_cocoa
def test_run_with_cocoa_menubar_signal_handler_survives_closed_loop(monkeypatch) -> None:
    async def _fake_main(cfg, menubar_backend, *, external_stop_event=None):
        raise RuntimeError("fallo antes de construir nada")

    monkeypatch.setattr(voice_client, "main", _fake_main)

    signal_calls: list[tuple] = []
    monkeypatch.setattr(
        voice_client.signal, "signal",
        lambda sig, handler: signal_calls.append((sig, handler)),
    )

    menubar = _SpyMenubarBackend()
    cfg = _make_cfg()

    voice_client._run_with_cocoa_menubar(cfg, menubar)

    assert len(signal_calls) == 2
    _sig, handler = signal_calls[0]
    handler(_sig, None)  # no debe lanzar RuntimeError: Event loop is closed


# ---------------------------------------------------------------------------
# Issue #20: SIGINT/SIGTERM no se procesaban hasta el siguiente tick del
# NSTimer (latencia hasta 1s con refresh_hz=1.0). El fix despierta el run
# loop de Cocoa vía signal.set_wakeup_fd() + NSFileHandle leyendo del pipe.
# ---------------------------------------------------------------------------


@pytestmark_cocoa
def test_run_with_cocoa_menubar_sets_wakeup_fd(monkeypatch) -> None:
    """CPython solo invoca un handler de signal cuando el hilo principal
    ejecuta bytecode Python — en Cocoa mode el hilo está bloqueado en
    NSApp.run(). signal.set_wakeup_fd() hace que CPython escriba al pipe
    cuando llega una señal; el watcher de NSFileHandle en el run loop
    despierta NSApp inmediatamente. Sin esto, SIGTERM puede tardar
    hasta 1/refresh_hz en procesarse (peor caso: 1s con refresh_hz=1.0).

    El unit test verifica solo el wiring (set_wakeup_fd + pipe); la
    latencia real se mide empíricamente arrancando voice_client.py como
    subprocess y midiendo SIGTERM → "Apagando".
    """
    async def _fake_main(cfg, menubar_backend, *, external_stop_event=None):
        raise RuntimeError("fallo antes de construir nada")

    monkeypatch.setattr(voice_client, "main", _fake_main)
    monkeypatch.setattr(
        voice_client.signal, "signal",
        lambda sig, handler: None,
    )

    # Capturar set_wakeup_fd.
    wakeup_calls: list[int] = []
    monkeypatch.setattr(
        voice_client.signal, "set_wakeup_fd",
        lambda fd: wakeup_calls.append(fd),
    )

    # Stub de Foundation: NSNotificationCenter y NSFileHandle son objetos
    # PyObjC reales y no se pueden monkey-patchear en atributos individuales.
    # En su lugar, los sustituimos antes de importar voice_client.

    menubar = _SpyMenubarBackend()
    cfg = _make_cfg()

    voice_client._run_with_cocoa_menubar(cfg, menubar)

    # 1. set_wakeup_fd debe llamarse con un fd no-negativo (extremo de
    #    escritura del pipe que CPython usará para despertarse).
    assert len(wakeup_calls) == 1, (
        f"signal.set_wakeup_fd debe llamarse una vez (Cocoa mode); "
        f"se llamó {len(wakeup_calls)} veces: {wakeup_calls}"
    )
    assert wakeup_calls[0] is not None and wakeup_calls[0] >= 0, (
        f"set_wakeup_fd debe recibir el extremo de escritura del pipe; "
        f"got {wakeup_calls[0]}"
    )


async def _test_menubar_stops_after_async_cleanup(monkeypatch) -> None:
    cleanup_order: list[str] = []

    class _AudioBackend:
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            cleanup_order.append("audio.stop")

    class _OwwBackend:
        async def run_forever(self, audio, on_wake) -> None:
            await asyncio.Future()

        async def disconnect(self) -> None:
            cleanup_order.append("oww.disconnect")

    class _GatewayClient:
        def __init__(self, cfg, device_id) -> None:
            pass

        async def disconnect(self) -> None:
            cleanup_order.append("gateway.disconnect")

    class _IdleClient:
        def __init__(self, *args) -> None:
            pass

        async def run(self, *args) -> None:
            await asyncio.Future()

    class _MenubarBackend(_SpyMenubarBackend):
        def stop(self) -> None:
            cleanup_order.append("menubar.stop")

    async def _idle_control_server(*args) -> None:
        await asyncio.Future()

    async def _completed_state_machine(*args) -> None:
        pass

    monkeypatch.setattr(registry, "make_audio", lambda cfg: _AudioBackend())
    monkeypatch.setattr(registry, "make_oww", lambda cfg, on_wake_word: _OwwBackend())
    monkeypatch.setattr(registry, "make_display", lambda cfg: object())
    monkeypatch.setattr(voice_client, "GatewayClient", _GatewayClient)
    monkeypatch.setattr(voice_client, "DisplayClient", _IdleClient)
    monkeypatch.setattr(voice_client, "MenubarClient", _IdleClient)
    monkeypatch.setattr(voice_client.control_server, "run", _idle_control_server)
    monkeypatch.setattr(voice_client, "sm_run", _completed_state_machine)

    await voice_client.main(
        _make_cfg(), _MenubarBackend(), external_stop_event=asyncio.Event()
    )

    assert cleanup_order == [
        "audio.stop",
        "gateway.disconnect",
        "oww.disconnect",
        "menubar.stop",
    ]


def test_menubar_stops_after_async_cleanup(monkeypatch) -> None:
    asyncio.run(_test_menubar_stops_after_async_cleanup(monkeypatch))
