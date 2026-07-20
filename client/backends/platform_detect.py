"""Detección de la plataforma Termux/Android — TERMUX_HOSTS_PATH sigue siendo
la fuente única de verdad para eso; is_termux() es un helper legacy.

TERMUX_HOSTS_PATH: usado por voice_client.py para parchear resolución DNS
con /etc/hosts de Termux.

is_termux(): desde Fase A (2026-07-20), registry.py YA NO usa esta función
para elegir el backend de audio por defecto — usa
core/platform_key.py::detect_platform() (basado en la variable de entorno
PREFIX, testeable sin tocar el filesystem). is_termux() se deja aquí sin
llamadores en producción hasta que Fase 6 sustituya este módulo del todo
(issue #60); no lo reintroduzcas para selección de backend sin antes
consolidar con detect_platform() — son dos mecanismos de detección
independientes que podrían divergir.
"""
from __future__ import annotations

import os

TERMUX_HOSTS_PATH = "/data/data/com.termux/files/usr/etc/hosts"


def is_termux() -> bool:
    """True si se está corriendo dentro de Termux (Android).

    Legacy: comprueba la existencia física de TERMUX_HOSTS_PATH. Sin
    llamadores en producción desde Fase A — ver docstring del módulo.
    """
    return os.path.exists(TERMUX_HOSTS_PATH)
