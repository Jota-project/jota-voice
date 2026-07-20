"""Detección de plataforma — fuente única de verdad.

Preferencia de detección de Termux (en orden):
  1. Variable de entorno PREFIX (recomendado — testeable sin filesystem).
  2. Existencia de TERMUX_HOSTS_PATH en el filesystem (fallback robusto
     para entornos donde PREFIX no se reenvía al proceso hijo, p.ej. init
     systems que sanitizan variables).
  3. Asume linux desktop.

Para el resto de plataformas, solo sys.platform.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Literal

# Constante única en backends.platform_detect (sigue viva porque
# app/voice_client.py la usa para parchear resolución DNS en Termux).
# Fase A revisión: detect_platform() también la usa como fallback para
# detectar Termux sin depender de PREFIX (resistente a init systems
# que sanitizan variables de entorno).
from backends.platform_detect import TERMUX_HOSTS_PATH


class UnsupportedPlatformError(Exception):
    """La plataforma detectada no tiene ningún PlatformKey conocido."""


@dataclass(frozen=True)
class PlatformKey:
    family: Literal["darwin", "termux", "linux", "windows"]
    variant: Literal["desktop", "mobile", "server"]


def detect_platform() -> PlatformKey:
    if sys.platform == "darwin":
        return PlatformKey("darwin", "desktop")
    if "com.termux" in os.environ.get("PREFIX", ""):
        return PlatformKey("termux", "mobile")
    if os.path.exists(TERMUX_HOSTS_PATH):
        return PlatformKey("termux", "mobile")
    if sys.platform.startswith("linux"):
        return PlatformKey("linux", "desktop")
    if sys.platform.startswith("win"):
        return PlatformKey("windows", "desktop")
    raise UnsupportedPlatformError(sys.platform)
