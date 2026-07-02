"""Registro y factory de backends.

Selecciona implementación concreta de AudioBackend / DisplayBackend / OwWBackend
según sys.platform + overrides de Config.
"""
from __future__ import annotations

import os
import sys

from config import Config

from .errors import ConfigError


def _default_audio_backend() -> str:
    s = sys.platform
    if s == "darwin" or s.startswith("linux"):
        prefix = os.environ.get("PREFIX", "")
        if "com.termux" in prefix:
            return "termux"
        return "sounddevice"
    if s.startswith("android"):
        return "termux"
    raise ConfigError(
        f"SO no soportado: {s!r}. Instala el backend de audio correspondiente."
    )


def make_audio(cfg: Config):
    from .audio_sounddevice import SounddeviceBackend
    from .audio_termux import TermuxBackend

    name = cfg.audio.backend or _default_audio_backend()
    if name == "sounddevice":
        return SounddeviceBackend(cfg.audio)
    if name == "termux":
        return TermuxBackend(cfg.audio)
    raise ConfigError(f"audio backend desconocido: {name!r}")


def make_display(cfg: Config):
    from .display_http import HttpDisplayBackend
    from .display_null import NullDisplayBackend

    name = cfg.display.backend or ("http" if cfg.display.url else "null")
    if name == "http":
        return HttpDisplayBackend(cfg.display)
    if name == "null":
        return NullDisplayBackend()
    raise ConfigError(f"display backend desconocido: {name!r}")


def make_oww(cfg: Config, on_wake_word):
    from .oww_wyoming import WyomingBackend

    return WyomingBackend(cfg.oww, on_wake_word)
