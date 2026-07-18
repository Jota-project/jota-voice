"""Registro y factory de backends.

Selecciona implementación concreta de AudioBackend / DisplayBackend / OwWBackend
según sys.platform + overrides de Config.
"""
from __future__ import annotations

import logging
import sys

from config import Config

from .errors import ConfigError
from .platform_detect import is_termux


def _default_audio_backend() -> str:
    if is_termux():
        return "termux"
    s = sys.platform
    if s == "darwin" or s.startswith("linux"):
        return "sounddevice"
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

    name = cfg.oww.backend
    if name == "wyoming":
        return WyomingBackend(cfg.oww, on_wake_word, audio_cfg=cfg.audio)
    raise ConfigError(f"oww backend desconocido: {name!r}")


def make_menubar(cfg: Config):
    from ui.menubar_null import NullMenubarBackend

    if not cfg.menubar.enabled:
        return NullMenubarBackend()

    if sys.platform != "darwin":
        return NullMenubarBackend()

    try:
        from ui.menubar_cocoa import CocoaMenubarBackend
    except ImportError:
        logging.getLogger(__name__).warning(
            "pyobjc-framework-Cocoa no disponible; menubar UI desactivada. "
            "Instala con: pip install pyobjc-framework-Cocoa"
        )
        return NullMenubarBackend()

    return CocoaMenubarBackend(cfg.menubar)
