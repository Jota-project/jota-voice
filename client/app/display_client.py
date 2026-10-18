"""DisplayClient — suscriptor del EventBus que traduce VoiceEvent a mensajes
del protocolo display (docs/display-protocol.md) sobre un DisplayBackend.

``backend.send`` es síncrono y no bloquea (encola), así que este bucle drena
el bus al ritmo al que se publica y un display lento nunca acumula backlog
en la cola del EventBus (issue #64): cada backend acota su propia cola.
"""
from __future__ import annotations

import logging
from typing import Any

from domain.event_bus import EventBus, VoiceEvent

log = logging.getLogger(__name__)

# Aviso informativo que se limpia solo. jota-gateway#227 hace llegar
# `status transcriber degraded` falsos en reposo: no deben quedarse fijos ni
# parecer un error.
DEGRADED_TTL_S = 20.0

_HEALTH_OK = frozenset({"restored", "connected", "recovered"})
_HEALTH_WARNING = frozenset({"unavailable", "reconnecting", "disconnected"})


def health_from_gateway_status(data: dict[str, Any]) -> dict[str, Any]:
    """Mapea un `gateway_status` del bus a un mensaje `health`.

    - restored/connected -> ok (limpia el aviso).
    - unavailable/reconnecting/disconnected -> warning, hasta que llegue el ok.
    - degraded (solo transcriber: calidad, no conectividad) y cualquier
      estado desconocido -> info con caducidad (DEGRADED_TTL_S).
    """
    service = str(data.get("service") or "gateway")
    state = str(data.get("state") or "")
    key = "gateway" if service == "gateway" else f"gateway.{service}"
    if state in _HEALTH_OK:
        level, ttl = "ok", None
    elif state in _HEALTH_WARNING:
        level, ttl = "warning", None
    else:
        level, ttl = "info", DEGRADED_TTL_S
    detail = {k: v for k, v in data.items() if k not in ("service", "state", "type")}
    return {
        "key": key, "level": level, "service": service, "state": state,
        "ttl_s": ttl, "detail": detail,
    }


class DisplayClient:
    def __init__(self, backend) -> None:
        self._backend = backend

    async def run(self, bus: EventBus, events=None) -> None:
        """Loop suscrito al bus. Se cancela externamente (asyncio.CancelledError).

        ``events``: suscripción ya creada con ``bus.subscribe()`` antes de
        arrancar la task (voice_client lo hace antes del connect inicial para
        no perder los `status` previos a `ready`). Si es None, se suscribe
        aquí."""
        async for event in (events if events is not None else bus.subscribe()):
            if not isinstance(event.data, dict):
                log.warning("DisplayClient: evento %s con data no dict ignorado", event.type)
                continue
            try:
                self._handle(event)
            except Exception:
                # Un evento malformado no puede dejar el display congelado.
                log.exception("DisplayClient: error traduciendo %s", event.type)

    def _send(self, type: str, data: dict[str, Any]) -> None:
        try:
            self._backend.send(type, data)
        except Exception:
            # Un backend defectuoso no puede tumbar la task del display.
            log.exception("DisplayClient: el backend falló con %s", type)

    def _handle(self, event: VoiceEvent) -> None:
        t, d = event.type, event.data
        if t == "recording_started":
            self._send("state", {"value": "listening", "text": ""})
        elif t == "transcription":
            self._send("state", {"value": "thinking", "text": d.get("text", "")})
        elif t == "turn_start":
            self._send("turn_start", {"turn_id": d.get("turn_id"), "turn_seq": d.get("turn_seq")})
        elif t == "playback_started":
            self._send("state", {"value": "response", "text": ""})
        elif t == "display_text_update":
            # Un evento por chunk de TTS (ver PlaybackEngine). El display
            # anima el reveal con chunk_text + duration_s; cumulative_text
            # permite resincronizar sin histórico.
            self._send("text_update", {
                "chunk_text": d.get("chunk_text", ""),
                "cumulative_text": d.get("cumulative_text", ""),
                "duration_s": d.get("duration_s", 0.0),
            })
        elif t == "tool_call":
            self._send("tool_call", {
                "phase": d.get("phase"),
                "name": d.get("name"),
                "tool_call_id": d.get("tool_call_id"),
                "args": d.get("args"),
                "result": d.get("result"),
                "is_error": d.get("is_error"),
            })
        elif t == "error":
            # Issue #91: antes un turno fallido no daba ningún feedback al
            # display. Es un evento, no un estado: el `idle` llega justo
            # detrás y el display decide cuánto tiempo mostrarlo.
            self._send("error", {
                "code": d.get("code"),
                "message": d.get("message", ""),
                "fatal": bool(d.get("fatal", False)),
            })
        elif t == "cancelled":
            self._send("cancelled", {"state": d.get("state")})
        elif t == "state_changed":
            if str(d.get("state", "")).lower() == "idle":
                self._send("state", {"value": "idle", "text": ""})
        elif t == "gateway_status":
            self._send("health", health_from_gateway_status(d))
        elif t == "oww_degraded":
            self._send("health", {
                "key": "oww", "level": "warning", "service": "oww",
                "state": "degraded", "ttl_s": None, "detail": dict(d),
            })
        elif t == "oww_recovered":
            self._send("health", {
                "key": "oww", "level": "ok", "service": "oww",
                "state": "recovered", "ttl_s": None, "detail": {},
            })
