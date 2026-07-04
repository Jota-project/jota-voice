"""Detección de la plataforma Termux/Android — fuente única de verdad.

Usado tanto por registry.py (elegir TermuxBackend por defecto) como por
voice_client.py (parchear resolución DNS con /etc/hosts de Termux).
"""
from __future__ import annotations

import os

TERMUX_HOSTS_PATH = "/data/data/com.termux/files/usr/etc/hosts"


def is_termux() -> bool:
    """True si se está corriendo dentro de Termux (Android)."""
    return os.path.exists(TERMUX_HOSTS_PATH)
