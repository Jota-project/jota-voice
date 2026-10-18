"""HttpDisplayBackend — POST /state al kiosk de jota-display.

COMPATIBILIDAD: se mantiene mientras jota-display no migre al protocolo
WebSocket (docs/display-protocol.md). El kiosk (jota-display/server/server.py,
puerto 8766) guarda tal cual el `state` que recibe y su frontend solo
entiende idle/listening/thinking/response, así que este backend es un
adaptador con pérdida del protocolo display:

  - ``state`` con uno de esos cuatro valores -> POST {"state", "text"}.
  - ``text_update`` -> POST {"state": "response", "text": <acumulado>}, con
    un reveal local durante ``duration_s`` (el kiosk recibía antes el texto
    a ticks de 50 ms desde PlaybackEngine; ahora llega un mensaje por chunk).
  - Todo lo demás (turn_start, tool_call, error, cancelled, health) se
    descarta: un `state` desconocido pisaría STATE["state"] en el kiosk y
    rompería su auto-apagado de pantalla.

Hardening:
  - #64: ``send`` solo encola. Una cola acotada (se descarta lo más antiguo)
    y los text_update consecutivos se fusionan, así un kiosk lento no
    acumula backlog ni frena a DisplayClient.
  - #59: el POST corre en un executor PROPIO de un hilo, con
    ``asyncio.wait_for`` como timeout total (urlopen no cubre la resolución
    DNS). Si el hilo sigue colgado, los POST siguientes se descartan en vez
    de apilarse detrás de él, y el executor por defecto del loop (compartido
    con el resto de la app) nunca se bloquea.
  - #58: URL validada al construir; sin url se usa DEFAULT_DISPLAY_HTTP_URL.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import math
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from config import DEFAULT_DISPLAY_HTTP_URL, DisplayConfig

from .display_base import STATES
from .errors import ConfigError

log = logging.getLogger(__name__)


@dataclass
class _Item:
    payload: dict[str, str]
    # Solo text_update: texto revelado en este chunk y su duración de audio.
    chunk_text: str = ""
    duration_s: float = 0.0

    @property
    def is_text_update(self) -> bool:
        return bool(self.chunk_text) or self.duration_s > 0


class HttpDisplayBackend:
    """Envía estado al kiosk de jota-display vía HTTP POST. Falla en silencio."""

    MAX_PENDING = 16
    # Margen sobre timeout_s para el timeout total (DNS + conexión + lectura).
    _TOTAL_TIMEOUT_MARGIN_S = 1.0

    def __init__(self, cfg: DisplayConfig) -> None:
        url = cfg.url or DEFAULT_DISPLAY_HTTP_URL
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ConfigError(
                f"display.url inválida para el backend http: {url!r} "
                "(se espera p.ej. 'http://127.0.0.1:8766')"
            )
        self._url = url.rstrip("/") + "/state"
        self._timeout = cfg.timeout_s
        self._tick_s = 0.1
        self._pending: deque[_Item] = deque()
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._inflight: concurrent.futures.Future | None = None

    # --- ciclo de vida ------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="display-http"
        )
        self._task = asyncio.create_task(self._run(), name="display_http_sender")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._executor is not None:
            # wait=False: un hilo colgado en DNS no debe retrasar el apagado.
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    # --- DisplayBackend -----------------------------------------------------

    def send(self, type: str, data: dict[str, Any]) -> None:
        if type == "state":
            value = data.get("value", "")
            if value not in STATES:
                return
            item = _Item({"state": value, "text": str(data.get("text", "") or "")})
        elif type == "text_update":
            item = _Item(
                {"state": "response", "text": str(data.get("cumulative_text", "") or "")},
                chunk_text=str(data.get("chunk_text", "") or ""),
                duration_s=max(float(data.get("duration_s", 0.0) or 0.0), 0.0),
            )
        else:
            return

        last = self._pending[-1] if self._pending else None
        if item.payload["state"] == "response" and last is not None \
                and last.payload["state"] == "response" and last.is_text_update \
                and item.is_text_update:
            # Fusiona con el text_update pendiente: el reveal arranca donde
            # empezaba el anterior y dura lo que ambos chunks juntos.
            prev_base = len(last.payload["text"]) - len(last.chunk_text)
            full = item.payload["text"]
            self._pending[-1] = _Item(
                item.payload,
                chunk_text=full[prev_base:] if prev_base <= len(full) else item.chunk_text,
                duration_s=last.duration_s + item.duration_s,
            )
        else:
            if len(self._pending) >= self.MAX_PENDING:
                dropped = self._pending.popleft()
                log.debug("HttpDisplayBackend: cola llena, descartado %r", dropped.payload)
            self._pending.append(item)
        self._wakeup.set()

    # --- sender ---------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            await self._wakeup.wait()
            self._wakeup.clear()
            while self._pending:
                item = self._pending.popleft()
                if item.is_text_update and item.chunk_text and item.duration_s > 0:
                    await self._reveal(item)
                else:
                    await self._post(item.payload)

    async def _reveal(self, item: _Item) -> None:
        full = item.payload["text"]
        chunk = item.chunk_text
        base = max(len(full) - len(chunk), 0)
        steps = max(1, min(len(chunk), int(item.duration_s / self._tick_s)))
        delay = item.duration_s / steps
        for i in range(1, steps + 1):
            visible = full[: base + math.ceil(len(chunk) * i / steps)]
            await self._post({"state": "response", "text": visible})
            if i < steps:
                await asyncio.sleep(delay)
                if self._pending:
                    # Llegó algo nuevo (el siguiente chunk, o idle): manda eso.
                    return

    async def _post(self, payload: dict[str, str]) -> None:
        if self._executor is None:
            return
        if self._inflight is not None and not self._inflight.done():
            log.debug(
                "HttpDisplayBackend: POST anterior aún colgado; descartado %r", payload
            )
            return
        try:
            self._inflight = self._executor.submit(self._do_post, payload)
        except RuntimeError:  # executor cerrado (apagado)
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(self._inflight)),
                timeout=self._timeout + self._TOTAL_TIMEOUT_MARGIN_S,
            )
        except asyncio.TimeoutError:
            log.debug("HttpDisplayBackend: timeout enviando %r al display", payload)
        except Exception as exc:
            log.debug("HttpDisplayBackend: display no disponible: %s", exc)

    def _do_post(self, payload: dict[str, str]) -> None:
        """POST bloqueante (corre en el executor propio)."""
        req = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout):
            pass
