"""Utilidades asyncio compartidas.

``cancel_and_wait`` existe porque el patrón que se repetía por el código::

    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass

se traga también la cancelación de la tarea *que espera*: si a la tarea
exterior le llega un ``cancel()`` justo mientras está en ``await t``, el
``CancelledError`` que recibe es el suyo, el ``except`` lo descarta y la
tarea exterior sigue viva. Con un bucle alrededor (``_oww_loop``) eso es
un cuelgue en el apagado.

``asyncio.wait`` no propaga el resultado ni la excepción de las tareas
esperadas, así que la única ``CancelledError`` que puede salir de aquí es
la de la tarea que llama, que se propaga como debe.
"""
from __future__ import annotations

import asyncio


async def cancel_and_wait(*tasks: asyncio.Future | None) -> None:
    """Cancela las tareas no terminadas y espera a que terminen.

    Nunca se traga la cancelación de la tarea que llama. Las excepciones de
    las tareas canceladas no se propagan; quien las necesite debe leer
    ``task.exception()`` después.
    """
    pending = [t for t in tasks if t is not None and not t.done()]
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.wait(pending)
    # Marca como recuperadas las excepciones de tareas ya terminadas para no
    # generar "Task exception was never retrieved" en el log.
    for task in tasks:
        if task is not None and task.done() and not task.cancelled():
            task.exception()
