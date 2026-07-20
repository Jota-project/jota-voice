"""Registro y factory de backends.

Selecciona implementación concreta de AudioBackend / DisplayBackend / OwWBackend
según PlatformKey (detect_platform) + overrides de Config.
"""
from __future__ import annotations

import logging
import sys

from config import Config
from core.platform_key import UnsupportedPlatformError, detect_platform

from .errors import ConfigError


def _default_audio_backend() -> str:
    try:
        platform_key = detect_platform()
    except UnsupportedPlatformError as exc:
        # Contrato del módulo: cualquier SO no soportado -> ConfigError
        # (no la UnsupportedPlatformError interna de core.platform_key).
        raise ConfigError(
            f"SO no soportado: {exc!s}. Instala el backend de audio correspondiente."
        ) from exc
    if platform_key.family == "termux":
        return "termux"
    if platform_key.family in ("darwin", "linux"):
        return "sounddevice"
    raise ConfigError(
        f"SO no soportado: {platform_key.family!r}. Instala el backend de audio correspondiente."
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

    # Hallazgo de la revisión multi-agente post-Fase A: este factory usaba
    # sys.platform directamente mientras _default_audio_backend() (3 líneas
    # arriba) ya migró a detect_platform().family. Alinear para que
    # cualquier refinamiento de la detección de plataforma en
    # core/platform_key.py se aplique a todo el módulo de una vez.
    try:
        platform_key = detect_platform()
    except UnsupportedPlatformError:
        return NullMenubarBackend()
    if platform_key.family != "darwin":
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
