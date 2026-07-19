"""Detección de plataforma — fuente única de verdad basada en sys.platform + PREFIX.

A diferencia de backends/platform_detect.py::is_termux() (que comprueba la
existencia de un path físico hardcodeado), detect_platform() usa la variable
de entorno PREFIX, testeable con monkeypatch sin tocar el filesystem real.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Literal


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
    if sys.platform.startswith("linux"):
        return PlatformKey("linux", "desktop")
    if sys.platform.startswith("win"):
        return PlatformKey("windows", "desktop")
    raise UnsupportedPlatformError(sys.platform)
