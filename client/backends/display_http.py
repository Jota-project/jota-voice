"""HttpDisplayBackend — POST a jota-display (mismo formato que display_client.py)."""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

from config import DisplayConfig

log = logging.getLogger(__name__)


class HttpDisplayBackend:
    """Envía estado a jota-display via HTTP POST. Falla silenciosamente."""

    def __init__(self, cfg: DisplayConfig) -> None:
        self._cfg = cfg
        self._url = cfg.url.rstrip("/") + "/state"
        self._timeout = cfg.timeout_s

    async def update(self, state: str, **kwargs) -> None:
        text = kwargs.get("text", "")
        payload = json.dumps({"state": state, "text": text}).encode()

        def _do_post() -> None:
            req = urllib.request.Request(
                self._url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout):
                pass

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, _do_post)
        except Exception as exc:
            log.debug("HttpDisplayBackend: display no disponible: %s", exc)