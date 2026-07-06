"""
control_server.py — Servidor HTTP de control de jota-voice.

Expone POST /cancel en localhost para que jota-display (u otros clientes)
puedan cancelar el turn activo. Usa asyncio puro, sin dependencias externas.
"""

from __future__ import annotations

import asyncio
import logging

from config import ControlConfig

log = logging.getLogger(__name__)


async def run(cfg: ControlConfig, cancel_event: asyncio.Event) -> None:
    """Arranca el servidor y sirve hasta que la task asyncio sea cancelada."""
    try:
        server = await asyncio.start_server(
            lambda r, w: _handle(r, w, cancel_event),
            host="127.0.0.1",
            port=cfg.port,
        )
    except OSError as exc:
        log.warning(
            "ControlServer: no se pudo arrancar en puerto %d: %s — cancel por botón desactivado",
            cfg.port,
            exc,
        )
        return

    addr = server.sockets[0].getsockname()
    log.info("ControlServer escuchando en %s:%d", addr[0], addr[1])
    async with server:
        await server.serve_forever()


async def _handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    cancel_event: asyncio.Event,
) -> None:
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        parts = request_line.decode(errors="replace").strip().split()
        method = parts[0] if len(parts) > 0 else ""
        path = parts[1] if len(parts) > 1 else ""

        # Leer headers hasta línea vacía
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if line in (b"\r\n", b"\n", b""):
                break

        if method == "POST" and path == "/cancel":
            cancel_event.set()
            log.info("ControlServer: cancel recibido")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        else:
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")

        await writer.drain()
    except Exception as exc:
        log.debug("ControlServer: error en conexión: %s", exc)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
