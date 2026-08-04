import asyncio
import json
import logging
import os
import warnings
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from domain.event_bus import EventBus

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
    def __init__(
        self,
        cfg: GatewayConfig,
        *,
        bus: Optional["EventBus"] = None,
        device_id: str | None = None,
    ) -> None:
        self._cfg = cfg
        self._bus = bus
        # Atributos poblados tras handshake exitoso (issue #24).
        self._session_id: str | None = None
        self._agent: str | None = None
        self._capabilities: dict[str, Any] = {}
        self._current_turn_seq: int = 0
        self._ws: Any | None = None
        # device_id se mantiene por compat con voice_client.main; se ignora
        # porque el handshake del gateway no lo contempla (ver
        # jota-gateway/docs/client-protocol.md §1). Remoción total en una
        # versión futura cuando todos los call sites estén actualizados.
        if device_id is not None:
            warnings.warn(
                "GatewayClient: device_id se ignora y se eliminará en una "
                "versión futura (issue #24). El handshake del gateway no "
                "lo contempla.",
                DeprecationWarning,
                stacklevel=2,
            )
            self._legacy_device_id: str | None = device_id
        else:
            self._legacy_device_id = None

    @property
    def is_connected(self) -> bool:
        """True si el WS está abierto. False si no se ha conectado o
        si se cerró tras un error. Consumido por state_machine._recording()
        para detectar reconexión perezosa (issue #24)."""
        return self._ws is not None

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
                    open_timeout=self._cfg.connect_timeout_s,
                ),
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
            # Issue #24: parseo completo de ready. session_id, agent,
            # requested_capabilities y live_capabilities se guardan para
            # correlación y para barge-in (issue #110 de paso).
            self._session_id = msg.get("session_id")
            self._agent = msg.get("agent")
            self._capabilities = {
                "requested": msg.get("requested_capabilities", {}),
                "live": msg.get("live_capabilities", {}),
            }
            log.debug(
                "Gateway listo (device=? session=%s agent=%s live=%s)",
                self._session_id, self._agent, self._capabilities.get("live"),
            )
            if self._bus is not None:
                from domain.event_bus import VoiceEvent
                self._bus.publish(VoiceEvent(
                    type="gateway_ready",
                    data={
                        "session_id": self._session_id,
                        "agent": self._agent,
                        "capabilities": self._capabilities,
                    },
                ))
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
                    # Framing documentado en jota-gateway/docs/client-protocol.md
                    # §6: [0xA1][turn_seq uint16 BE][PCM16 24kHz].
                    if len(message) < 3 or message[0] != 0xA1:
                        log.warning(
                            "Gateway: frame binario inesperado (%d bytes, primer byte=%r)",
                            len(message), message[0] if message else None,
                        )
                        continue
                    frame_seq = (message[1] << 8) | message[2]
                    # Issue #24: descarte de audio de turnos anteriores. Si el
                    # gateway ya anunció un turn_start con seq mayor, este
                    # frame es del turno cancelado y no debe reproducirse.
                    if frame_seq < self._current_turn_seq:
                        log.debug(
                            "Gateway: descartando tts_chunk con turn_seq=%d "
                            "(current=%d, turno cancelado)",
                            frame_seq, self._current_turn_seq,
                        )
                        continue
                    pcm16 = message[3:]
                    yield GatewayEvent(
                        type="tts_chunk", data={"audio": pcm16, "turn_seq": frame_seq}
                    )
                    continue
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    log.warning("Gateway: frame JSON inválido: %r", message[:80])
                    continue
                event_type = data.get("type", "")
                # Issue #24: turn_start actualiza _current_turn_seq SOLO si seq > current.
                # Es la única fuente de incremento de _current_turn_seq (defensa contra
                # gateways desincronizados).
                if event_type == "turn_start":
                    new_seq = data.get("turn_seq", 0)
                    if isinstance(new_seq, int) and new_seq > self._current_turn_seq:
                        self._current_turn_seq = new_seq
                    else:
                        log.warning(
                            "Gateway: turn_start con seq <= current "
                            "(current=%d, got=%r)",
                            self._current_turn_seq, new_seq,
                        )
                if event_type in ("done", "turn_end"):
                    return
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
