import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator

import websockets

from config import GatewayConfig

log = logging.getLogger(__name__)


@dataclass
class GatewayEvent:
    type: str
    data: dict


class GatewayClient:
    def __init__(self, cfg: GatewayConfig) -> None:
        self._cfg = cfg
        self._ws = None

    async def connect(self) -> None:
        self._ws = await asyncio.wait_for(
            websockets.connect(self._cfg.ws_url),
            timeout=self._cfg.connect_timeout_s,
        )
        handshake = {"type": "handshake", "client_key": self._cfg.client_key}
        await self._ws.send(json.dumps(handshake))
        log.debug("Gateway conectado a %s", self._cfg.ws_url)

    async def disconnect(self) -> None:
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def send_audio(self, float32_bytes: bytes) -> None:
        await self._ws.send(float32_bytes)

    async def send_end(self) -> None:
        await self._ws.send(json.dumps({"type": "end"}))
        log.debug("Gateway: enviado end")

    async def receive(self) -> AsyncIterator[GatewayEvent]:
        async for message in self._ws:
            if isinstance(message, bytes):
                # Gateway envía audio TTS como JSON con base64, no como bytes crudos.
                # Si aun así llegan bytes, los ignoramos silenciosamente.
                log.debug("Gateway: frame binario inesperado (%d bytes), ignorado", len(message))
                continue
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                log.warning("Gateway: frame JSON inválido: %r", message[:80])
                continue
            event_type = data.get("type", "")
            if event_type == "done":
                return
            if event_type == "tts_chunk" and "audio" in data:
                data = dict(data, audio=base64.b64decode(data["audio"]))
            yield GatewayEvent(type=event_type, data=data)
