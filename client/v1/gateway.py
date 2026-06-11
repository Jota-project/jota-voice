import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Optional

import websockets

from config import GatewayConfig

log = logging.getLogger(__name__)


@dataclass
class GatewayEvent:
    kind: Literal["transcription_partial", "transcription", "token", "service_status", "audio_chunk"]
    text: Optional[str] = None
    content: Optional[str] = None
    audio: Optional[bytes] = None
    raw: Optional[dict] = field(default=None, repr=False)


class GatewayClient:
    def __init__(self, cfg: GatewayConfig) -> None:
        self._cfg = cfg
        self._ws = None

    @property
    def is_connected(self) -> bool:
        if self._ws is None:
            return False
        try:
            return self._ws.open
        except AttributeError:
            return not self._ws.closed

    async def connect(self) -> None:
        self._ws = await asyncio.wait_for(
            websockets.connect(self._cfg.ws_url),
            timeout=self._cfg.connect_timeout_s,
        )
        handshake = {
            "client_key": self._cfg.client_key,
            "input_mode": "audio",
            "output_mode": ["audio", "text", "status"],
        }
        await self._ws.send(json.dumps(handshake))
        log.debug("Gateway conectado a %s", self._cfg.ws_url)

    async def disconnect(self) -> None:
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def send_audio_chunk(self, float32_bytes: bytes) -> None:
        await self._ws.send(float32_bytes)

    async def send_end(self) -> None:
        await self._ws.send(json.dumps({"type": "end"}))
        log.debug("Gateway: enviado end")

    async def receive(self) -> AsyncIterator[GatewayEvent]:
        async for message in self._ws:
            if isinstance(message, bytes):
                yield GatewayEvent(kind="audio_chunk", audio=message)
            else:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    log.warning("Gateway: frame JSON inválido: %r", message[:80])
                    continue
                kind = data.get("type", "")
                if kind == "transcription_partial":
                    yield GatewayEvent(kind="transcription_partial", text=data.get("text"), raw=data)
                elif kind == "transcription":
                    yield GatewayEvent(kind="transcription", text=data.get("text"), raw=data)
                elif kind == "token":
                    yield GatewayEvent(kind="token", content=data.get("content"), raw=data)
                elif kind == "service_status":
                    yield GatewayEvent(kind="service_status", raw=data)
                else:
                    log.debug("Gateway: tipo desconocido %r", kind)
