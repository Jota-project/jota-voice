"""
display_client.py — Suscriptor del EventBus que traduce VoiceEvent a POSTs HTTP
hacia jota-display.

Mapeo de eventos → estados de display:
  recording_started            → "listening"
  transcription                → "thinking"  (text = transcripción)
  playback_started             → "response"
  display_text_update          → "response"  (text = texto sincronizado)
  state_changed {to:"IDLE"}    → "idle"
  (resto de eventos se ignoran)
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from typing import Optional

from config import DisplayConfig
from event_bus import EventBus, VoiceEvent

log = logging.getLogger(__name__)


class DisplayClient:
    def __init__(self, cfg: DisplayConfig, bus: EventBus) -> None:
        self._cfg = cfg
        self._bus = bus

    async def run(self) -> None:
        """Loop suscrito al bus. Se cancela externamente (asyncio.CancelledError)."""
        async for event in self._bus.subscribe():
            await self._handle(event)

    async def _handle(self, event: VoiceEvent) -> None:
        state: Optional[str] = None
        text: str = ""

        if event.type == "recording_started":
            state = "listening"
        elif event.type == "transcription":
            state = "thinking"
            text = event.data.get("text", "")
        elif event.type == "playback_started":
            state = "response"
        elif event.type == "display_text_update":
            state = "response"
            text = event.data.get("text", "")
        elif event.type == "state_changed":
            # Soporta tanto {"state": "idle"} como {"to": "IDLE"} / {"to": "idle"}
            raw_state = event.data.get("state", "")
            raw_to = event.data.get("to", "")
            if raw_state.lower() == "idle" or raw_to.lower() == "idle":
                state = "idle"

        if state is not None:
            await self._post(state, text)

    async def _post(self, state: str, text: str) -> None:
        payload = json.dumps({"state": state, "text": text}).encode()
        url = self._cfg.url + "/state"
        timeout = self._cfg.timeout_s

        def _do_post() -> None:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout):
                pass

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, _do_post)
        except Exception as exc:
            log.debug("Display no disponible: %s", exc)
