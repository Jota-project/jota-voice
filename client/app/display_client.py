"""DisplayClient — suscriptor del EventBus que traduce VoiceEvent a llamadas
sobre un DisplayBackend inyectado.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from domain.event_bus import EventBus, VoiceEvent

log = logging.getLogger(__name__)


class DisplayClient:
    def __init__(self, backend) -> None:
        self._backend = backend
        self._bus: Optional[EventBus] = None

    async def run(self, bus: EventBus) -> None:
        """Loop suscrito al bus. Se cancela externamente (asyncio.CancelledError)."""
        self._bus = bus
        async for event in bus.subscribe():
            await self._handle(event)

    async def _handle(self, event: VoiceEvent) -> None:
        if not isinstance(event.data, dict):
            return

        if event.type == "recording_started":
            await self._backend.update("listening")
        elif event.type == "transcription":
            text = event.data.get("text", "")
            await self._backend.update("thinking", text=text)
        elif event.type == "playback_started":
            await self._backend.update("response")
        elif event.type == "display_text_update":
            text = event.data.get("text", "")
            await self._backend.update("response", text=text)
        elif event.type == "state_changed":
            raw_state = event.data.get("state", "").lower()
            if raw_state == "idle":
                await self._backend.update("idle")