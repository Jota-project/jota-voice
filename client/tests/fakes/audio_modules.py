"""Superficies falsas de las librerías de audio nativas (issue #75).

pyaudio solo existe en Termux y sounddevice necesita PortAudio, así que los
tests sustituyen el módulo en ``sys.modules``. Estas clases son el *spec* de
los mocks: un test que llame a algo que la librería real no tiene falla en
vez de pasar en silencio. Los fixtures que las instalan (con teardown vía
monkeypatch) están en ``tests/conftest.py``.
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock, create_autospec

PA_INT16 = 8  # valor real de pyaudio.paInt16


class PyAudioSpec:
    """Superficie de ``pyaudio.PyAudio`` que usa TermuxBackend."""

    def open(self, format, channels, rate, output):  # noqa: A002, ARG002
        ...

    def terminate(self) -> None:
        ...


class PaStreamSpec:
    """Superficie de ``pyaudio.Stream`` (salida) que usa TermuxBackend."""

    def write(self, data: bytes, num_frames: int | None = None) -> None:  # noqa: ARG002
        ...

    def stop_stream(self) -> None:
        ...

    def close(self) -> None:
        ...


class FakePyAudio(types.SimpleNamespace):
    """Lo que devuelve el fixture ``fake_pyaudio``."""

    module: types.ModuleType
    cls: MagicMock
    instance: MagicMock


def make_fake_pyaudio() -> FakePyAudio:
    """Módulo ``pyaudio`` falso: ``PyAudio`` es un autospec de PyAudioSpec y
    cada ``open()`` devuelve un stream autospec nuevo."""
    pa_cls = create_autospec(PyAudioSpec)
    pa_instance = pa_cls.return_value
    pa_instance.open.side_effect = lambda **_: create_autospec(PaStreamSpec, instance=True)
    module = types.ModuleType("pyaudio")
    module.PyAudio = pa_cls  # type: ignore[attr-defined]
    module.paInt16 = PA_INT16  # type: ignore[attr-defined]
    return FakePyAudio(module=module, cls=pa_cls, instance=pa_instance)
