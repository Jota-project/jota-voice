"""Colas de captura acotadas (issue #113)."""
from __future__ import annotations

import asyncio

from core.audio.queues import QUEUE_MAX_SECONDS, capture_queue_maxsize, put_latest


def test_maxsize_cubre_los_segundos_configurados() -> None:
    # 16 kHz, 512 muestras por frame: 31,25 frames/s.
    assert capture_queue_maxsize(16000, 512) == 313
    assert QUEUE_MAX_SECONDS == 10.0


def test_maxsize_nunca_es_cero() -> None:
    assert capture_queue_maxsize(1, 10_000) == 1


def test_put_latest_descarta_el_mas_antiguo_si_esta_llena() -> None:
    q: asyncio.Queue[int] = asyncio.Queue(maxsize=3)
    for i in range(5):
        put_latest(q, i)
    assert [q.get_nowait() for _ in range(q.qsize())] == [2, 3, 4]


def test_put_latest_con_hueco_solo_encola() -> None:
    q: asyncio.Queue[int] = asyncio.Queue(maxsize=3)
    put_latest(q, 1)
    assert q.qsize() == 1
