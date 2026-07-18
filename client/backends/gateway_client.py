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
            # max_size=None desactiva el límite de 1 MiB del default de websockets:
            # un tts_chunk grande o un mensaje de control voluminoso dispararía
            # 1009 (payload too big) y un cierre anómalo silencioso. El framing
            # binario TTS ([0xA1][seq][PCM16]) no acota tamaño por chunk — el
            # gateway puede serializar varios segundos de audio en un único frame.
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    self._cfg.ws_url,
                    additional_headers=headers or None,
                    max_size=None,
                ),
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
        # Protocolo (jota-gateway/docs/client-protocol.md): leer respuesta del
        # handshake antes de enviar audio. Sin esto, client_key inválida →
        # close 1008 → el cliente envía el turno entero (hasta 15s) a una
        # conexión muerta y solo lo descubre al final, de forma genérica.
        # Coste: un RTT extra por turno (arquitectura reconnect-per-turn); #24
        # migrará a sesión WS persistente donde el handshake ocurre una vez.
        # Deuda técnica conocida (no expandido): capabilities.tts/barge_in/
        # transcriber y session_id del ready no se capturan — relevante cuando
        # #24 haga la sesión persistente.
        try:
            raw = await asyncio.wait_for(
                self._ws.recv(),
                timeout=self._cfg.connect_timeout_s,
            )
        except asyncio.TimeoutError:
            log.error(
                "Gateway: timeout esperando 'ready' tras handshake (%.1fs)",
                self._cfg.connect_timeout_s,
            )
            raise
        msg = json.loads(raw)
        msg_type = msg.get("type", "")
        if msg_type == "ready":
            log.debug("Gateway listo (device=%s)", self._device_id)
            return
        if msg_type == "error":
            raise RuntimeError(
                f"Gateway: handshake rechazado: {msg.get('message', msg)}"
            )
        raise RuntimeError(
            f"Gateway: handshake devolvió tipo inesperado {msg_type!r}: {msg}"
        )

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
                    # Framing documentado en jota-gateway/docs/client-protocol.md:
                    # [0xA1][turn_seq uint16 BE][PCM16 24kHz]. Sin separar la
                    # cabecera, el PCM llega desalineado y con tamaño potencialmente
                    # impar — np.frombuffer(..., dtype=int16) revienta.
                    if len(message) < 3 or message[0] != 0xA1:
                        log.warning(
                            "Gateway: frame binario inesperado (%d bytes, primer byte=%r)",
                            len(message), message[0] if message else None,
                        )
                        continue
                    turn_seq = (message[1] << 8) | message[2]
                    pcm16 = message[3:]
                    yield GatewayEvent(
                        type="tts_chunk", data={"audio": pcm16, "turn_seq": turn_seq}
                    )
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
        except websockets.exceptions.ConnectionClosedOK:
            # Cierre limpio (1000/1001). El turno ya terminó vía turn_end;
            # la sesión puede continuar en otro turno. Terminar en silencio
            # es lo correcto — state_machine ya publicó playback_ended.
            return
        except websockets.exceptions.ConnectionClosedError as exc:
            # Cierre anómalo (1006 abnormal closure, 1009 payload too big,
            # 1011 internal error…). Antes del fix de #16 se tragaba con un
            # `return` y state_machine.publicaba playback_ended "todo bien" —
            # el usuario oía la respuesta cortarse sin saber qué pasó. Ahora
            # se propaga para que run() publique VoiceEvent(type='error').
            log.warning("Gateway: conexión cerrada con error: %s", exc)
            raise
