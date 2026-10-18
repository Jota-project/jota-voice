"""Tests de core.aio.cancel_and_wait."""
from __future__ import annotations

import asyncio

from core.aio import cancel_and_wait


async def _sleeper(cleanup_s: float = 0.0) -> None:
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        if cleanup_s:
            # Simula un hijo que tarda en limpiar tras el cancel.
            await asyncio.sleep(cleanup_s)
        raise


def test_cancel_and_wait_cancela_y_espera() -> None:
    async def _t() -> None:
        child = asyncio.create_task(_sleeper())
        await asyncio.sleep(0)
        await cancel_and_wait(child, None)
        assert child.cancelled()

    asyncio.run(_t())


def test_cancel_and_wait_no_se_traga_la_cancelacion_del_llamante() -> None:
    """El patrón viejo `cancel(); try: await t except CancelledError: pass`
    se tragaba el cancel de la tarea exterior si llegaba durante el await."""

    async def _t() -> None:
        child = asyncio.create_task(_sleeper(cleanup_s=0.2))
        await asyncio.sleep(0)
        survived = []

        async def _outer() -> None:
            while True:  # como _oww_loop: si se traga el cancel, no sale nunca
                await cancel_and_wait(child)
                survived.append(True)
                await asyncio.sleep(0.01)

        outer = asyncio.create_task(_outer())
        await asyncio.sleep(0.05)  # outer está dentro de cancel_and_wait
        outer.cancel()
        done, _ = await asyncio.wait([outer], timeout=1.0)
        assert outer in done and outer.cancelled()
        assert not survived

    asyncio.run(_t())


def test_cancel_and_wait_recupera_excepciones_sin_propagarlas() -> None:
    async def _boom() -> None:
        raise RuntimeError("x")

    async def _t() -> None:
        t = asyncio.create_task(_boom())
        await asyncio.sleep(0)
        await cancel_and_wait(t)
        assert isinstance(t.exception(), RuntimeError)

    asyncio.run(_t())
