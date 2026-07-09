import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import AsyncGenerator, Optional, Any

import websockets

from config import GatewayConfig

log = logging.getLogger(__name__)


def _cloudflare_access_headers() -> dict[str, str]:
    """Cabeceras de Service Token para Cloudflare Access, si están configuradas.

    Desactivado por defecto; se activa poniendo CF_ACCESS_CLIENT_ID y
    CF_ACCESS_CLIENT_SECRET (p.ej. en devices/<id>/.env, ver config.py).
    """
    client_id = os.environ.get("CF_ACCESS_CLIENT_ID")
    client_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET")
    if client_id and client_secret:
        return {
            "CF-Access-Client-Id": client_id,
            "CF-Access-Client-Secret": client_secret,
        }
    return {}


@dataclass
class GatewayEvent:
    type: str
    data: dict


class GatewayClient:
    def __init__(self, cfg: GatewayConfig, device_id: str = "unknown") -> None:
        self._cfg = cfg
        self._device_id = device_id
        self._ws: Optional[Any] = None

    async def connect(self) -> None:
        headers = _cloudflare_access_headers()
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self._cfg.ws_url, additional_headers=headers or None),
                timeout=self._cfg.connect_timeout_s,
            )
        except asyncio.TimeoutError:
            log.error("Gateway: timeout conectando a %s (%.1fs)", self._cfg.ws_url, self._cfg.connect_timeout_s)
            raise
        handshake = {
            "client_key": self._cfg.client_key,
            "device_id": self._device_id,
            "input_mode": "audio",
            "output_mode": ["audio", "text", "status"],
        }
        await self._ws.send(json.dumps(handshake))
        log.debug("Gateway conectado a %s (device=%s)", self._cfg.ws_url, self._device_id)

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

    async def send_cancel(self) -> None:
        if self._ws is None:
            raise RuntimeError("GatewayClient: no conectado")
        await self._ws.send(json.dumps({"type": "cancel"}))
        log.debug("Gateway: enviado cancel")

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
                if event_type in ("done", "turn_end"):
                    # El gateway real (green-house) señaliza fin de turno
                    # con "turn_end" en vez de "done" — sin esto, receive()
                    # se quedaba esperando mensajes que nunca llegaban hasta
                    # el timeout de 30s de RESPONDING en cada turno.
                    return
                # El gateway envía "token" para LLM; normalizamos al tipo interno.
                if event_type == "token":
                    event_type = "llm_token"
                yield GatewayEvent(type=event_type, data=data)
        except websockets.exceptions.ConnectionClosed as exc:
            log.warning("Gateway: conexión cerrada inesperadamente: %s", exc)
            return
