import asyncio
import json
import logging
from dataclasses import dataclass
from typing import AsyncGenerator, Optional, Any

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
        self._ws: Optional[Any] = None

    async def connect(self) -> None:
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self._cfg.ws_url),
                timeout=self._cfg.connect_timeout_s,
            )
        except asyncio.TimeoutError:
            log.error("Gateway: timeout conectando a %s (%.1fs)", self._cfg.ws_url, self._cfg.connect_timeout_s)
            raise
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

    async def send_audio(self, float32_bytes: bytes) -> None:
        if self._ws is None:
            raise RuntimeError("GatewayClient: no conectado")
        await self._ws.send(float32_bytes)

    async def send_end(self) -> None:
        if self._ws is None:
            raise RuntimeError("GatewayClient: no conectado")
        await self._ws.send(json.dumps({"type": "end"}))
        log.debug("Gateway: enviado end")

    async def send_text(self, text: str) -> None:
        """Envía la transcripción confirmada al gateway para disparar el orquestador."""
        if self._ws is None:
            raise RuntimeError("GatewayClient: no conectado")
        await self._ws.send(json.dumps({"type": "send", "text": text}))
        log.debug("Gateway: enviado send %r", text[:60])

    async def receive(self) -> AsyncGenerator[GatewayEvent, None]:
        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    yield GatewayEvent(type="tts_chunk", data={"audio": message})
                    continue
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    log.warning("Gateway: frame JSON inválido: %r", message[:80])
                    continue
                event_type = data.get("type", "")
                if event_type == "done":
                    return
                # El gateway envía "token" para LLM; normalizamos al tipo interno.
                if event_type == "token":
                    event_type = "llm_token"
                yield GatewayEvent(type=event_type, data=data)
        except websockets.exceptions.ConnectionClosed as exc:
            log.warning("Gateway: conexión cerrada inesperadamente: %s", exc)
            return
