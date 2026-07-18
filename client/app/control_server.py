"""
control_server.py — Servidor HTTP de control de jota-voice.

Expone POST /cancel en localhost para que jota-display (u otros clientes)
puedan cancelar el turn activo. Usa asyncio puro, sin dependencias externas.

Requiere un token compartido (header X-Jota-Control-Token) en toda petición:
un navegador nunca puede fijar un header custom sin forzar un preflight
CORS, y este servidor no implementa CORS — así que cualquier fetch()
lanzado por JS de terceros en una pestaña del usuario queda bloqueado por
el propio navegador antes de llegar aquí. El token protege además contra
un atacante local que hable HTTP crudo directamente al puerto.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import secrets
from pathlib import Path

from config import ControlConfig

log = logging.getLogger(__name__)

TOKEN_HEADER = "x-jota-control-token"
DEFAULT_TOKEN_PATH = os.path.expanduser("~/.jota-voice/control_token")


class _RateLimiter:
    """Ventana fija: máximo N peticiones por M segundos."""

    def __init__(self, max_requests: int, window_s: float) -> None:
        self._max_requests = max_requests
        self._window_s = window_s
        self._timestamps: list[float] = []

    def allow(self) -> bool:
        now = asyncio.get_running_loop().time()
        cutoff = now - self._window_s
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        if len(self._timestamps) >= self._max_requests:
            return False
        self._timestamps.append(now)
        return True


def _load_or_create_token(path: Path) -> str:
    if path.is_file():
        return path.read_text().strip()

    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Otro proceso ganó la carrera entre el is_file() de arriba y este
        # open() — usamos el token que ya escribió, no lo pisamos.
        return path.read_text().strip()
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token


async def _read_headers(reader: asyncio.StreamReader) -> dict[str, str]:
    headers: dict[str, str] = {}
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if line in (b"\r\n", b"\n", b""):
            break
        decoded = line.decode(errors="replace")
        name, sep, value = decoded.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return headers


async def run(cfg: ControlConfig, cancel_event: asyncio.Event) -> None:
    """Arranca el servidor y sirve hasta que la task asyncio sea cancelada."""
    token_path = Path(cfg.token_path or DEFAULT_TOKEN_PATH)
    token = _load_or_create_token(token_path)
    limiter = _RateLimiter(cfg.rate_limit_max_requests, cfg.rate_limit_window_s)

    try:
        server = await asyncio.start_server(
            lambda r, w: _handle(r, w, cancel_event, token, limiter),
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
    token: str,
    limiter: _RateLimiter,
) -> None:
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        parts = request_line.decode(errors="replace").strip().split()
        method = parts[0] if len(parts) > 0 else ""
        path = parts[1] if len(parts) > 1 else ""

        headers = await _read_headers(reader)

        if not limiter.allow():
            writer.write(b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 0\r\n\r\n")
        elif not hmac.compare_digest(headers.get(TOKEN_HEADER, "").encode(), token.encode()):
            writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
        elif method == "POST" and path == "/cancel":
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
