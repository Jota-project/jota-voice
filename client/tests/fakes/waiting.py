"""Esperas deterministas para tests asyncio (issue #76).

En vez de ``await asyncio.sleep(0.05)`` "para que dé tiempo", los tests
esperan a la condición que de verdad les importa. El timeout es solo una
red de seguridad generosa: con la máquina cargada el test tarda más, pero
no falla.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable

SAFETY_TIMEOUT_S = 5.0

# Referencia capturada al importar: sigue siendo la real aunque un test
# parchee asyncio.sleep para controlar el código bajo prueba.
_real_sleep = asyncio.sleep


async def wait_until(
    predicate: Callable[[], bool],
    timeout: float = SAFETY_TIMEOUT_S,
    interval: float = 0.001,
    message: str = "",
) -> None:
    """Cede el loop hasta que ``predicate()`` sea cierto.

    Mide el timeout con time.monotonic (no con loop.time) para seguir
    funcionando si un test sustituye el reloj del código bajo prueba.
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(
                f"condición no cumplida tras {timeout}s" + (f": {message}" if message else "")
            )
        await _real_sleep(interval)
