"""display_ws.py — WsDisplayBackend: servidor WebSocket local para displays.

Implementación de referencia del protocolo display (docs/display-protocol.md,
la fuente de verdad para cualquier display nuevo: HUD de macOS, kiosk web...).
Sustituye al antiguo ControlServer (POST /cancel en el 8765): un único
servidor en 127.0.0.1 que

  - empuja a cada display conectado los mensajes que produce DisplayClient
    (estado, turno, texto por chunk, tool calls, errores, health);
  - manda primero un ``sync`` con el snapshot completo al conectar, así que
    reconectar siempre converge al estado real sin histórico;
  - recibe comandos del display por la misma conexión (``cancel``,
    ``resync``);
  - admite varios displays a la vez, cada uno con su cola acotada: un
    display lento recibe un ``sync`` fresco en vez de frenar a los demás o
    al pipeline (issue #64).

Seguridad (portada del ControlServer, con #106 y #115 resueltas):
  - Token compartido en un fichero 0600 (``~/.jota-voice/display_token``),
    NUEVO en cada arranque y nunca vacío (#108, #115: se publica con
    os.replace desde un temporal ya escrito; ``start()`` falla cerrado si
    no hay token).
  - El token viaja en la cabecera ``X-Jota-Display-Token``: la API
    WebSocket de un navegador no puede fijar cabeceras, así que una página
    web no puede abrir esta conexión (ni por cross-site WebSocket hijacking).
  - Rate limit SOLO para handshakes con token inválido (#106): un escaneo
    recibe 429, el cliente legítimo nunca.
  - Límite de displays simultáneos (503) y de tamaño de mensaje entrante.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import websockets
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.http11 import Request, Response

from config import DisplayConfig

from .display_base import MESSAGE_TYPES, STATES

log = logging.getLogger(__name__)

TOKEN_HEADER = "X-Jota-Display-Token"
DEFAULT_TOKEN_PATH = os.path.expanduser("~/.jota-voice/display_token")
HOST = "127.0.0.1"

# Versión del protocolo display (docs/display-protocol.md §2). Va en el
# `sync`: un display la compara antes de asumir ningún shape de mensaje.
PROTOCOL_VERSION = 1

# Tamaño máximo de un mensaje display -> servidor (los comandos son JSON de
# unas decenas de bytes).
MAX_INCOMING_BYTES = 4096

# Apagado: espera del cierre ordenado de cada conexión (websockets) y del
# servidor completo antes de abortar transportes. stop() queda acotado a
# unos 2 s aunque un display no lea.
CLOSE_TIMEOUT_S = 1.0
STOP_TIMEOUT_S = 1.0

# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------


def _rotate_token(path: Path) -> str:
    """Genera un token nuevo (64 hex) y lo publica en ``path`` con permisos
    0600. Se llama en cada start(): el token da acceso a transcripciones,
    respuestas y tool calls, así que uno filtrado deja de valer al reiniciar.

    #115: se escribe en un temporal 0600 del mismo directorio y se publica
    con ``os.replace`` (atómico): un lector ve el token anterior completo o
    el nuevo completo, nunca un fichero vacío. Sustituye cualquier fichero
    previo (vacío, con permisos laxos o con un token viejo).
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(token)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    return token


# ---------------------------------------------------------------------------
# Rate limit de handshakes fallidos (#106)
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Ventana fija: máximo N eventos por M segundos."""

    def __init__(self, max_events: int, window_s: float) -> None:
        self._max = max_events
        self._window_s = window_s
        self._timestamps: list[float] = []

    def allow(self) -> bool:
        now = time.monotonic()
        cutoff = now - self._window_s
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        if len(self._timestamps) >= self._max:
            return False
        self._timestamps.append(now)
        return True


# ---------------------------------------------------------------------------
# Servidor
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class _Client:
    conn: Any
    queue: asyncio.Queue
    writer: asyncio.Task | None = field(default=None)


class WsDisplayBackend:
    """DisplayBackend sobre WebSocket local. Ver docs/display-protocol.md."""

    # Mensajes pendientes por display antes de descartar su cola y mandarle
    # un `sync` (issue #64).
    CLIENT_QUEUE_MAX = 256

    def __init__(
        self, cfg: DisplayConfig, on_cancel: Callable[[], None] | None = None
    ) -> None:
        self._cfg = cfg
        self._on_cancel = on_cancel or (lambda: None)
        # El token se carga en start(): instanciar no toca el disco.
        self._token: str = ""
        self._fail_limiter = _RateLimiter(cfg.rate_limit_max_requests, cfg.rate_limit_window_s)
        self._server: Server | None = None
        self._clients: set[_Client] = set()
        self._seq = 0
        self._turn_id: str | None = None
        self._state = "idle"
        self._text = ""
        self._health: dict[str, dict[str, Any]] = {}
        self._health_timers: dict[str, asyncio.TimerHandle] = {}

    @property
    def port(self) -> int | None:
        """Puerto real en escucha (útil con port=0), o None si no arrancó."""
        if self._server is None or not self._server.sockets:
            return None
        return self._server.sockets[0].getsockname()[1]

    # --- ciclo de vida --------------------------------------------------------

    async def start(self) -> None:
        token_path = Path(self._cfg.token_path or DEFAULT_TOKEN_PATH)
        try:
            token = _rotate_token(token_path)
        except OSError as exc:
            token = ""
            log.error("WsDisplayBackend: no se pudo preparar el token (%s)", exc)
        if not token:
            # Falla cerrado: sin token no vacío no se sirve (#115).
            log.error("WsDisplayBackend: sin token; servidor de displays desactivado")
            return
        self._token = token
        try:
            self._server = await serve(
                self._handle_connection,
                HOST,
                self._cfg.port,
                process_request=self._process_request,
                max_size=MAX_INCOMING_BYTES,
                close_timeout=CLOSE_TIMEOUT_S,
            )
        except OSError as exc:
            log.warning(
                "WsDisplayBackend: no se pudo escuchar en %s:%d (%s); displays y "
                "cancel externo desactivados en esta sesión",
                HOST, self._cfg.port, exc,
            )
            self._server = None
            return
        log.info("WsDisplayBackend escuchando en ws://%s:%d", HOST, self.port)

    async def stop(self) -> None:
        for handle in self._health_timers.values():
            handle.cancel()
        self._health_timers.clear()
        server, self._server = self._server, None
        # Primero los writers: uno bloqueado en send() hacia un display que
        # no lee retrasaría el cierre.
        for client in list(self._clients):
            await self._unregister(client)
        if server is None:
            return
        # Apagado acotado (launchd mata a los 20 s y aún quedan audio, gateway
        # y OWW por cerrar): cierre ordenado con close_timeout corto y, si un
        # display no colabora, abort del transporte.
        server.close()
        try:
            await asyncio.wait_for(server.wait_closed(), STOP_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.warning("WsDisplayBackend: displays sin cerrar a tiempo; abortando conexiones")
            for conn in list(server.connections):
                conn.transport.abort()
            try:
                await asyncio.wait_for(server.wait_closed(), STOP_TIMEOUT_S)
            except asyncio.TimeoutError:
                log.warning("WsDisplayBackend: el servidor no terminó de cerrar")

    # --- handshake ------------------------------------------------------------

    async def _process_request(
        self, connection: ServerConnection, request: Request
    ) -> Response | None:
        # Exactamente un valor: headers.get() lanza MultipleValuesError con
        # la cabecera repetida (antes: 500 con traceback, sin rate limit).
        values = request.headers.get_all(TOKEN_HEADER)
        presented = values[0] if len(values) == 1 else ""
        if self._token and presented and hmac.compare_digest(
            presented.encode(), self._token.encode()
        ):
            if len(self._clients) >= self._cfg.max_clients:
                return connection.respond(503, "too many displays\n")
            return None  # seguir con el upgrade
        # #106: solo los intentos fallidos consumen cuota.
        if not self._fail_limiter.allow():
            return connection.respond(429, "rate limited\n")
        return connection.respond(401, "invalid token\n")

    # --- conexiones -------------------------------------------------------------

    def _register(self, conn: Any) -> _Client:
        """Alta síncrona: el `sync` entra en la cola antes que cualquier
        broadcast posterior, así que siempre es el primer mensaje."""
        client = _Client(conn=conn, queue=asyncio.Queue(maxsize=self.CLIENT_QUEUE_MAX))
        client.queue.put_nowait(self._sync_message())
        client.writer = asyncio.create_task(self._writer(client), name="display_ws_writer")
        self._clients.add(client)
        log.info("WsDisplayBackend: display conectado (%d activos)", len(self._clients))
        return client

    async def _unregister(self, client: _Client) -> None:
        if client in self._clients:
            self._clients.discard(client)
            log.info("WsDisplayBackend: display desconectado (%d activos)", len(self._clients))
        writer, client.writer = client.writer, None
        if writer is not None and writer is not asyncio.current_task():
            writer.cancel()
            try:
                await writer
            except asyncio.CancelledError:
                pass

    async def _handle_connection(self, connection: ServerConnection) -> None:
        client = self._register(connection)
        try:
            async for raw in connection:
                self._handle_command(client, raw)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await self._unregister(client)

    async def _writer(self, client: _Client) -> None:
        while True:
            envelope = await client.queue.get()
            try:
                # default=str: un valor no serializable en data (p.ej. en el
                # detail de un health) no puede matar al writer.
                await client.conn.send(json.dumps(envelope, default=str))
            except websockets.exceptions.ConnectionClosed:
                return
            except Exception:
                # Sin writer el display se quedaría colgado (conectado y
                # respondiendo pings, pero sin recibir nada): se cierra para
                # que reconecte y reciba un sync.
                log.exception("WsDisplayBackend: error enviando a un display; se cierra")
                try:
                    await client.conn.close(1011, "internal error")
                except Exception:
                    pass
                return

    def _handle_command(self, client: _Client, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError, UnicodeDecodeError):
            log.warning("WsDisplayBackend: comando no-JSON ignorado: %r", raw[:80])
            return
        if not isinstance(msg, dict):
            log.warning("WsDisplayBackend: comando con forma inválida ignorado")
            return
        cmd = msg.get("type")
        if cmd == "cancel":
            turn_id = msg.get("turn_id")
            if turn_id is not None and turn_id != self._turn_id:
                log.info(
                    "WsDisplayBackend: cancel del turno %r ignorado (turno actual %r)",
                    turn_id, self._turn_id,
                )
                return
            log.info("WsDisplayBackend: cancel recibido de un display")
            try:
                self._on_cancel()
            except Exception:
                log.exception("WsDisplayBackend: on_cancel falló")
        elif cmd == "resync":
            self._enqueue(client, self._sync_message())
        else:
            log.debug("WsDisplayBackend: comando desconocido ignorado: %r", cmd)

    # --- envío ------------------------------------------------------------------

    def _snapshot_health(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self._health.items()}

    def _sync_message(self) -> dict[str, Any]:
        return {
            "type": "sync",
            "seq": self._seq,
            "ts": time.time(),
            "turn_id": self._turn_id,
            "protocol_version": PROTOCOL_VERSION,
            "data": {
                "state": self._state,
                "text": self._text,
                "turn_id": self._turn_id,
                "health": self._snapshot_health(),
            },
        }

    def _enqueue(self, client: _Client, envelope: dict[str, Any]) -> None:
        try:
            client.queue.put_nowait(envelope)
        except asyncio.QueueFull:
            # Display atascado: lo pendiente ya no vale, el snapshot sí (y
            # refleja este mismo mensaje, aplicado antes de emitirlo).
            while not client.queue.empty():
                client.queue.get_nowait()
            client.queue.put_nowait(self._sync_message())
            log.warning("WsDisplayBackend: display lento, cola descartada; se le manda sync")

    def _broadcast(self, msg_type: str, data: dict[str, Any]) -> None:
        self._seq += 1
        envelope = {
            "type": msg_type,
            "seq": self._seq,
            "ts": time.time(),
            "turn_id": self._turn_id,
            "data": data,
        }
        for client in list(self._clients):
            self._enqueue(client, envelope)

    # --- health -----------------------------------------------------------------

    def _set_health(self, data: dict[str, Any]) -> bool:
        key = data.get("key")
        if not key:
            return False
        old = self._health_timers.pop(key, None)
        if old is not None:
            old.cancel()
        if data.get("level") == "ok":
            self._health.pop(key, None)
            return True
        self._health[key] = data
        ttl = data.get("ttl_s")
        if ttl:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return True
            self._health_timers[key] = loop.call_later(
                float(ttl), self._expire_health, key, data
            )
        return True

    def _expire_health(self, key: str, entry: dict[str, Any]) -> None:
        self._health_timers.pop(key, None)
        if self._health.get(key) is not entry:
            return
        del self._health[key]
        self._broadcast("health", {
            "key": key,
            "level": "ok",
            "service": entry.get("service"),
            "state": "expired",
            "ttl_s": None,
            "detail": {},
        })

    # --- DisplayBackend -----------------------------------------------------------

    def send(self, type: str, data: dict[str, Any]) -> None:
        """Aplica el mensaje al snapshot y lo encola para cada display. No
        bloquea nunca."""
        if type not in MESSAGE_TYPES:
            log.debug("WsDisplayBackend: tipo de mensaje desconocido %r", type)
            return
        data = dict(data)
        if type == "state":
            value = data.get("value")
            if value not in STATES:
                log.debug("WsDisplayBackend: estado desconocido %r", value)
                return
            self._state = value
            self._text = str(data.get("text", "") or "")
            if value == "idle":
                self._turn_id = None
                self._text = ""
        elif type == "turn_start":
            self._turn_id = data.get("turn_id")
        elif type == "text_update":
            self._state = "response"
            self._text = str(data.get("cumulative_text", "") or "")
        elif type == "health":
            if not self._set_health(data):
                return
        self._broadcast(type, data)
