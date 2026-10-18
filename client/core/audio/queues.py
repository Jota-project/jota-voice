"""Colas de captura acotadas (issue #113).

Las colas del fan-out de captura (RECORDING y OWW) no siempre tienen
consumidor: mientras IDLE espera la wake word nadie lee la de RECORDING, y
mientras OWW reconecta con backoff nadie lee la suya. Sin ``maxsize`` crecían
sin límite (~60 KB/s a 16 kHz float32, cientos de MB en horas de espera).

Se acotan a los últimos ``QUEUE_MAX_SECONDS`` de audio descartando lo más
antiguo: para el reconocimiento importa el audio reciente, y el estado
RECORDING ya descarta el backlog al empezar (el contexto previo lo da el
preroll). 10 s cubren de sobra los frames legítimos capturados durante el
connect() al gateway, que se conservan.
"""
from __future__ import annotations

import asyncio
import math
from typing import TypeVar

QUEUE_MAX_SECONDS = 10.0

T = TypeVar("T")


def capture_queue_maxsize(sample_rate: int, frames_per_buffer: int) -> int:
    """Número de frames que caben en ``QUEUE_MAX_SECONDS`` (mínimo 1)."""
    return max(1, math.ceil(QUEUE_MAX_SECONDS * sample_rate / frames_per_buffer))


def put_latest(q: asyncio.Queue[T], item: T) -> None:
    """Encola ``item``; si la cola está llena, descarta antes el más antiguo.

    Debe llamarse desde el hilo del loop (vía ``call_soon_threadsafe``)."""
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(item)
