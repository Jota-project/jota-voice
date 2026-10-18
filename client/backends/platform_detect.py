"""Detección de la plataforma Termux/Android.

TERMUX_HOSTS_PATH: usado por voice_client.py para parchear resolución DNS
con /etc/hosts de Termux, y por core/platform_key.py como señal de Termux.

termux_env_signal(): señales de entorno de Termux, compartidas con
core/platform_key.py::detect_platform() para que los dos mecanismos de
detección no diverjan (issue #60).

is_termux(): helper legacy. Desde Fase A (2026-07-20), registry.py YA NO usa
esta función para elegir el backend de audio por defecto — usa
core/platform_key.py::detect_platform(). No la reintroduzcas para selección
de backend: usa detect_platform().
"""
from __future__ import annotations

import os

TERMUX_HOSTS_PATH = "/data/data/com.termux/files/usr/etc/hosts"


def termux_env_signal() -> bool:
    """True si el entorno del proceso delata Termux.

    - ``PREFIX`` apunta dentro de com.termux (lo exporta la shell de Termux).
    - ``TERMUX_VERSION`` no vacía (la exporta la app de Termux; issue #60:
      segunda señal por si PREFIX no se reenvía o cambia el layout).
    """
    if "com.termux" in os.environ.get("PREFIX", ""):
        return True
    return bool(os.environ.get("TERMUX_VERSION", "").strip())


def is_termux() -> bool:
    """True si se está corriendo dentro de Termux (Android).

    Antes dependía solo de la existencia física de TERMUX_HOSTS_PATH (issue
    #60); ahora acepta también las señales de entorno de termux_env_signal().
    """
    return os.path.exists(TERMUX_HOSTS_PATH) or termux_env_signal()
