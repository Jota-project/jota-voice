import asyncio
import json
import logging
import urllib.request
from typing import Literal

from config import DisplayConfig

log = logging.getLogger(__name__)


class DisplayClient:
    def __init__(self, cfg: DisplayConfig) -> None:
        self._cfg = cfg

    async def set_state(
        self,
        state: Literal["idle", "listening", "thinking", "response"],
        text: str = "",
    ) -> None:
        loop = asyncio.get_event_loop()
        payload = json.dumps({"state": state, "text": text}).encode()
        url = self._cfg.url + "/state"
        timeout = self._cfg.timeout_s

        def _post():
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout):
                pass

        try:
            await loop.run_in_executor(None, _post)
        except Exception as exc:
            log.debug("Display no disponible: %s", exc)
