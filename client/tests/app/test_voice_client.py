"""test_voice_client.py — Tests focalizados del fix #11.

Cubre solo la lógica NUEVA: que un fallo de audio.start() quede visible en
el menubar y apague limpiamente en vez de propagar/colgar, y que el hilo de
asyncio de _run_with_cocoa_menubar no muera en silencio ni deje handlers de
señal que revienten sobre un loop ya cerrado.
"""
from __future__ import annotations

import asyncio

from app import voice_client
from backends import registry
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
