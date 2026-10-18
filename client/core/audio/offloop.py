"""Llamadas bloqueantes a drivers de audio fuera del event loop.

PortAudio (sounddevice, PyAudio) puede quedarse colgado en stop()/close()/
write() si el dispositivo desaparece (Bluetooth desconectado, HAL de Android
reiniciado). Estas llamadas no pueden correr en el hilo del loop.

No se usa el executor por defecto de asyncio a propósito: sus hilos no son
daemon y ``asyncio.run`` los espera al salir (``shutdown_default_executor``),
así que un driver colgado retendría el apagado igual. Aquí cada llamada va en
un hilo daemon propio, que no impide que el proceso termine.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable


def start_off_loop(fn: Callable[[], Any], name: str = "audio-off-loop") -> asyncio.Future:
    """Lanza ``fn()`` en un hilo daemon y devuelve un Future del loop actual
    que se resuelve con su resultado o su excepción.

    Cancelar el Future no detiene el hilo: quien necesite saber que la
    llamada terminó de verdad debe conservar el Future y esperarlo (con
    ``asyncio.shield`` si la espera puede cancelarse)."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()

    def _resolve(exc: BaseException | None, result: Any) -> None:
        if fut.done():
            return
        if exc is not None:
            fut.set_exception(exc)
        else:
            fut.set_result(result)

    def _runner() -> None:
        try:
            result, exc = fn(), None
        except BaseException as e:  # noqa: BLE001 - se reenvía al loop
            result, exc = None, e
        try:
            loop.call_soon_threadsafe(_resolve, exc, result)
        except RuntimeError:
            pass  # el loop ya cerró: nadie espera el resultado

    threading.Thread(target=_runner, daemon=True, name=name).start()
    return fut


async def call_off_loop(fn: Callable[[], Any], timeout: float, name: str = "audio-off-loop") -> Any:
    """Ejecuta ``fn()`` en un hilo daemon y espera como mucho ``timeout``.

    Lanza TimeoutError si ``fn`` no vuelve a tiempo (el hilo sigue dentro
    del driver: el llamador no debe volver a tocar ese recurso), o la
    excepción de ``fn``."""
    return await asyncio.wait_for(start_off_loop(fn, name), timeout)
