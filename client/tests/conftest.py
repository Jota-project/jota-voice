"""Fixtures compartidos de la suite (issue #75).

Reglas, porque este conftest se carga para TODOS los tests:

- Nada de fixtures ``autouse`` ni de tocar ``sys.modules`` al importar:
  cambiaría en silencio el comportamiento de ficheros que no los piden.
- Cada stub se instala con ``monkeypatch`` y se retira al acabar el test,
  así el resultado no depende del orden ni de qué ficheros se colecten.
"""
from __future__ import annotations

import sys
from typing import Any, Callable
from unittest.mock import MagicMock, create_autospec

import pytest

from tests.fakes.audio_modules import FakePyAudio, make_fake_pyaudio


@pytest.fixture
def ws_connect(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any], Any]:
    """Sustituye ``websockets.connect`` (lo que llama GatewayClient) solo
    durante el test. Uso: ``ws_connect(AsyncMock(return_value=ws))``;
    devuelve el mismo objeto para poder inspeccionar sus llamadas."""
    import websockets

    def _install(connect: Any) -> Any:
        monkeypatch.setattr(websockets, "connect", connect)
        return connect

    return _install


@pytest.fixture
def fake_pyaudio(monkeypatch: pytest.MonkeyPatch) -> FakePyAudio:
    """Módulo ``pyaudio`` falso con autospec (ver tests/fakes/audio_modules)."""
    fake = make_fake_pyaudio()
    monkeypatch.setitem(sys.modules, "pyaudio", fake.module)
    return fake


@pytest.fixture
def no_pyaudio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simula una plataforma sin pyaudio: ``import pyaudio`` lanza ImportError."""
    monkeypatch.setitem(sys.modules, "pyaudio", None)


@pytest.fixture
def fake_sounddevice(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Módulo ``sounddevice`` falso con el spec del real. ``PortAudioError``
    es la excepción real; ``InputStream`` es un autospec cuyo comportamiento
    decide cada test (``fake.InputStream.side_effect = ...``)."""
    import sounddevice as real_sd

    fake = create_autospec(real_sd, instance=False)
    fake.PortAudioError = real_sd.PortAudioError
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return fake
