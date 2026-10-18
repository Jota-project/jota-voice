import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from domain.event_bus import EventBus, EventType

import websockets
import websockets.exceptions
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


# ---------------------------------------------------------------------------
# Cierres de sesión (issue #118) y rechazos de handshake (issue #107)
# ---------------------------------------------------------------------------

# Código 1006 (abnormal closure) cuando no hubo close frame: corte de red,
# proceso del gateway muerto o la propia librería dando la conexión por
# perdida (keepalive ping sin pong).
CLOSE_ABNORMAL = 1006


def close_code_label(code: int | None) -> str:
    """Etiqueta legible del código de cierre, para logs y para la UI."""
    return {
        1000: "cierre normal (inactividad del cliente o cierre ordenado)",
        1001: "el gateway se está apagando",
        1006: "conexión perdida sin close frame (red o keepalive)",
        1008: "rechazo de política (auth o agente)",
        1011: "error interno del gateway",
        1012: "el gateway se está reiniciando",
        1013: "cliente demasiado lento",
    }.get(code or CLOSE_ABNORMAL, f"código {code}")


def close_is_fatal(code: int | None, reason: str = "") -> bool:
    """Solo un 1008 de auth/política NO se reintenta en segundo plano.

    El gateway lo usa para rechazos de autenticación y de política de
    agente (client-protocol.md §1): reintentar en bucle no lo arregla y
    solo llena logs. Todo lo demás (1000 inactividad, 1001/1012 reinicio,
    1011 error interno, 1013 cliente lento, 1006 corte) es recuperable
    abriendo una sesión nueva (§13). Dentro de 1008, ``agent_not_available``
    es transitorio (roster vacío durante un reconnect del gateway, #107) y
    se trata como recuperable, igual que en el handshake.
    """
    if code != 1008:
        return False
    return classify_handshake_close(code, reason) in _FATAL_HANDSHAKE_KINDS


class GatewayDisconnected(ConnectionError):
    """La sesión WebSocket con el gateway se cerró (por cualquier motivo).

    Sustituye a las ``websockets.exceptions.ConnectionClosed*`` fuera de
    este módulo: state_machine y voice_client no necesitan conocer la
    librería, y un cierre limpio (1000/1001) a mitad de turno ya no se
    confunde con "turno terminado bien".
    """

    def __init__(self, code: int | None, reason: str = "") -> None:
        self.code = code if code is not None else CLOSE_ABNORMAL
        self.reason = reason or ""
        self.fatal = close_is_fatal(self.code, self.reason)
        detail = f": {self.reason}" if self.reason else ""
        super().__init__(
            f"conexión con el gateway cerrada ({self.code}, "
            f"{close_code_label(self.code)}){detail}"
        )


# kinds de rechazo de handshake que no tiene sentido reintentar solos: hace
# falta intervención humana (config del device o del admin del gateway).
_FATAL_HANDSHAKE_KINDS = frozenset({
    "invalid_client_key",
    "agent_not_permitted",
    "handshake_invalid",
    "http_rejected",
    "policy_rejected",
})


def classify_handshake_close(code: int | None, reason: str) -> str:
    """Traduce (código, reason) del close frame del handshake a un ``kind``.

    El gateway NO manda un mensaje ``error`` antes de cerrar en el
    handshake (client-protocol.md §9, nota de precisión): la causa solo
    viaja en el ``reason`` del close frame, con los textos de la tabla del
    §1. Se compara por fragmentos estables, sin depender de mayúsculas ni
    del nombre del agente.
    """
    r = (reason or "").lower()
    if code == 1008:
        if "clave de cliente" in r or "client_key" in r or "client key" in r:
            return "invalid_client_key"
        if "not permitted" in r:
            return "agent_not_permitted"
        if "not available" in r:
            return "agent_not_available"
        if "timeout" in r:
            return "handshake_timeout"
        if "handshake" in r:
            return "handshake_invalid"
        return "policy_rejected"
    if code == 1011:
        return "service_unavailable"
    return "closed_during_handshake"


_HANDSHAKE_KIND_MESSAGES = {
    "invalid_client_key": "client_key inválida o cliente inactivo: revisa gateway.client_key",
    "agent_not_permitted": "el agente no está permitido para este cliente: lo restringe el admin (allowed_agents)",
    "agent_not_available": "el agente no está disponible en el gateway ahora mismo",
    "handshake_timeout": "el gateway no recibió el handshake a tiempo",
    "handshake_invalid": "el gateway rechazó el handshake por inválido",
    "policy_rejected": "el gateway rechazó la conexión por política",
    "service_unavailable": "servicio crítico del gateway no disponible",
    "http_rejected": "el servidor rechazó el upgrade a WebSocket (HTTP)",
    "error_message": "el gateway respondió al handshake con un error",
    "closed_during_handshake": "el gateway cerró la conexión durante el handshake",
}


class GatewayHandshakeError(RuntimeError):
    """El gateway rechazó o cortó el handshake antes de ``ready`` (issue #107).

    ``kind`` distingue la causa para que la UI dé un mensaje accionable;
    ``fatal`` indica que reintentar solo no lo va a arreglar.
    """

    def __init__(self, kind: str, code: int | None = None, reason: str = "") -> None:
        self.kind = kind
        self.code = code
        self.reason = reason or ""
        self.fatal = kind in _FATAL_HANDSHAKE_KINDS
        base = _HANDSHAKE_KIND_MESSAGES.get(kind, kind)
        extra = []
        if code is not None:
            extra.append(str(code))
        if self.reason:
            extra.append(repr(self.reason))
        suffix = f" ({', '.join(extra)})" if extra else ""
        super().__init__(f"Gateway: handshake rechazado: {base}{suffix}")


def _closed_info(exc: BaseException) -> tuple[int, str]:
    """(code, reason) de una ConnectionClosed de websockets."""
    rcvd = getattr(exc, "rcvd", None)
    if rcvd is not None:
        return rcvd.code, rcvd.reason or ""
    sent = getattr(exc, "sent", None)
    if sent is not None:
        return sent.code, sent.reason or ""
    return CLOSE_ABNORMAL, ""


# Tope para el close ordenado de un handshake fallido o cancelado. El
# close_timeout por defecto de websockets (10 s) retrasaba RECORDING si se
# cancelaba un handshake en curso; pasado este tope se aborta el transporte.
_ABORT_CLOSE_S = 0.5


async def _close_quickly(ws: Any) -> None:
    try:
        await asyncio.wait_for(ws.close(), timeout=_ABORT_CLOSE_S)
        return
    except Exception:
        pass
    transport = getattr(ws, "transport", None)
    abort = getattr(transport, "abort", None)
    if callable(abort):
        try:
            abort()
        except Exception:
            pass


class GatewayClient:
    def __init__(
        self,
        cfg: GatewayConfig,
        *,
        bus: Optional["EventBus"] = None,
    ) -> None:
        # Sin device_id (issue #83): el handshake documentado en
        # jota-gateway/docs/client-protocol.md §1 solo admite client_key,
        # input_mode, output_mode y agent; la identidad del device la da la
        # client_key. El parámetro estuvo deprecado e ignorado desde #24.
        self._cfg = cfg
        self._bus = bus
        # Atributos poblados tras handshake exitoso (issue #24).
        self._session_id: str | None = None
        self._agent: str | None = None
        self._capabilities: dict[str, Any] = {}
        self._current_turn_seq: int = 0
        # Tras un cancel, el audio en tránsito del turno cancelado tiene
        # seq == _current_turn_seq: se descarta todo frame con seq <= esto.
        self._discard_upto: int = 0
        self._ws: Any | None = None
        # Issue #118: estado coherente de la sesión.
        self._closed: bool = False
        self.last_close: GatewayDisconnected | None = None
        # Último rechazo fatal de handshake: mientras esté puesto, la
        # reconexión en segundo plano se detiene (solo reintenta un turno
        # pedido por el usuario). Se limpia con un connect() correcto.
        self.fatal_error: GatewayHandshakeError | None = None
        # Último rechazo fatal ya publicado en el bus. Compartido entre el
        # supervisor de IDLE (cada IDLE crea uno nuevo) y el turno, para no
        # avisar dos veces del mismo rechazo (revisión Fase R).
        self.reported_fatal: GatewayHandshakeError | None = None
        self._last_send_at: float = time.monotonic()
        self._connect_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Estado de la sesión
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """True solo si hay WS y no se ha observado su cierre.

        Antes miraba solo ``self._ws is not None``: tras un cierre limpio
        (1000/1001) el socket muerto seguía figurando como conectado y el
        cliente nunca reconectaba (issue #118).
        """
        if self._ws is None or self._closed:
            return False
        code = getattr(self._ws, "close_code", None)
        if isinstance(code, int):
            self._mark_closed(code, getattr(self._ws, "close_reason", "") or "")
            return False
        return True

    @property
    def seconds_since_last_send(self) -> float:
        return time.monotonic() - self._last_send_at

    def _mark_closed(self, code: int | None, reason: str = "") -> GatewayDisconnected:
        exc = GatewayDisconnected(code, reason if isinstance(reason, str) else "")
        if not self._closed:
            self._closed = True
            self.last_close = exc
            log.warning("Gateway: sesión cerrada — %s", exc)
            if exc.fatal:
                # 1008 a mitad de sesión: mismo trato que un rechazo fatal
                # del handshake (sin reintentos automáticos).
                err = GatewayHandshakeError(
                    classify_handshake_close(exc.code, exc.reason), exc.code, exc.reason
                )
                if err.fatal:
                    self.fatal_error = err
        return self.last_close or exc

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------

    def _connect_kwargs(self) -> dict[str, Any]:
        ping_interval = self._cfg.ping_interval_s or None
        ping_timeout = self._cfg.ping_timeout_s or None
        return dict(
            additional_headers=_cloudflare_access_headers() or None,
            # max_size=None desactiva el límite de 1 MiB del default de
            # websockets: un tts_chunk grande dispararía 1009 (payload too
            # big). El framing binario TTS no acota tamaño por chunk.
            max_size=None,
            # Issue #118: max_queue=None hace que la librería vacíe el socket
            # siempre, aunque la app lea al ritmo de reproducción (el
            # _receive_loop espera a play_chunk) o no lea (RECORDING). Con el
            # default (16 frames) la librería dejaba de leer, el pong del
            # keepalive quedaba detrás del audio en vuelo y a los ~40 s la
            # propia librería cerraba con 1011. También evita que el gateway
            # nos cierre con 1013 por cliente lento. Coste: el audio en vuelo
            # se acumula en memoria (≈48 KB/s de respuesta).
            max_queue=None,
            # Keepalive de protocolo explícito (client-protocol.md §13). Sirve
            # para detectar un peer muerto en IDLE; NO resetea el
            # IDLE_TIMEOUT_S del gateway (ver keepalive_interval_s).
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
            open_timeout=self._cfg.connect_timeout_s,
        )

    async def ensure_connected(self) -> None:
        """Conecta si la sesión no está viva. Seguro con llamadas concurrentes.

        El supervisor de IDLE y el inicio de turno pueden pedir la conexión a
        la vez; el lock hace que compartan un único intento.
        """
        async with self._connect_lock:
            if self.is_connected:
                return
            await self.connect()

    async def connect(self) -> None:
        # Una sesión nueva siempre parte de cero: cerrar la anterior si
        # quedaba algo y reiniciar el estado de turno (issue #118: el gateway
        # reinicia turn_seq en cada sesión; con el valor viejo se
        # descartarían los frames de la sesión nueva).
        if self._ws is not None:
            await self.disconnect()
        self._current_turn_seq = 0
        self._discard_upto = 0
        self._session_id = None
        self._agent = None
        self._capabilities = {}
        self._closed = False
        # El resultado del último intento manda: si este falla por algo
        # transitorio, el rechazo fatal anterior deja de bloquear la
        # reconexión en segundo plano; si vuelve a ser fatal, se re-marca.
        self.fatal_error = None
        try:
            await self._open_session()
        except GatewayHandshakeError as exc:
            # Único sitio que marca el rechazo fatal, para TODA causa
            # (close 1008 del handshake, `error` fatal, HTTP 401/403 en el
            # upgrade). Antes el 401/403 no lo marcaba y el supervisor de
            # IDLE reintentaba para siempre sin avisar (revisión Fase R).
            if exc.fatal:
                self.fatal_error = exc
            raise

    async def _open_session(self) -> None:
        # Un único plazo (connect_timeout_s) para abrir el socket Y recibir
        # `ready` (revisión Fase R): antes eran dos plazos completos seguidos
        # y _recording los recortaba con un tercero más corto.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._cfg.connect_timeout_s
        try:
            ws = await asyncio.wait_for(
                websockets.connect(self._cfg.ws_url, **self._connect_kwargs()),
                timeout=self._cfg.connect_timeout_s,
            )
        except asyncio.TimeoutError:
            log.error("Gateway: timeout conectando a %s (%.1fs)", self._cfg.ws_url, self._cfg.connect_timeout_s)
            raise
        except websockets.exceptions.InvalidStatus as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            kind = "http_rejected" if status in (401, 403) else "closed_during_handshake"
            raise GatewayHandshakeError(kind, status, str(exc)) from exc
        # self._ws solo se asigna tras `ready`: durante el handshake la
        # sesión NO está conectada (is_connected=False) y nadie más puede
        # leer ni enviar por ese socket.
        self._closed = True
        try:
            await self._handshake(ws, deadline)
        except BaseException:
            # Cualquier fallo (incluida la cancelación) cierra el socket:
            # sin esto quedaba una conexión medio abierta.
            await _close_quickly(ws)
            raise
        self._ws = ws
        self._closed = False
        self._last_send_at = time.monotonic()
        self.fatal_error = None

    async def _handshake(self, ws: Any, deadline: float) -> None:
        handshake = {
            "client_key": self._cfg.client_key,
            "input_mode": "audio",
            "output_mode": ["audio", "text", "status"],
        }
        # El handshake se envía nada más abrir el socket: el gateway cierra
        # con 1008 si tarda más de HANDSHAKE_TIMEOUT_S (issue #116, punto 1).
        await ws.send(json.dumps(handshake))
        # Protocolo (client-protocol.md §1/§2/§5): leer respuesta del
        # handshake antes de enviar audio. Pueden llegar `status` antes de
        # `ready` (health check, difusión del ClientRegistry): se reenvían al
        # bus y se sigue esperando, todo dentro de connect_timeout_s.
        loop = asyncio.get_running_loop()
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                remaining = 0.001
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                log.error(
                    "Gateway: timeout esperando 'ready' tras handshake (%.1fs)",
                    self._cfg.connect_timeout_s,
                )
                raise
            except websockets.exceptions.ConnectionClosed as exc:
                code, reason = _closed_info(exc)
                kind = classify_handshake_close(code, reason)
                err = GatewayHandshakeError(kind, code, reason)
                log.error("%s", err)
                raise err from exc
            if isinstance(raw, bytes):
                log.warning("Gateway: frame binario inesperado antes de 'ready'; ignorado")
                continue
            msg = json.loads(raw)
            msg_type = msg.get("type", "")
            if msg_type == "ready":
                self._on_ready(msg)
                return
            if msg_type == "status":
                log.info("Gateway: status antes de 'ready' → %r", msg)
                self._publish("gateway_status", msg)
                continue
            if msg_type == "error":
                # El `code` del gateway (AUTH_FAILED…) va en el reason: el
                # atributo .code es el close code WS (issue #113).
                reason = str(msg.get("message", msg))
                if msg.get("code"):
                    reason = f"{msg['code']}: {reason}"
                err = GatewayHandshakeError("error_message", None, reason)
                if msg.get("fatal") is True:
                    err.fatal = True
                raise err
            raise RuntimeError(
                f"Gateway: handshake devolvió tipo inesperado {msg_type!r}: {msg}"
            )

    def _on_ready(self, msg: dict) -> None:
        # Issue #24/#110: parseo completo de ready. El protocolo actual
        # (v1.15.0+, #114) separa requested_capabilities y live_capabilities;
        # `capabilities` a secas ya no existe.
        self._session_id = msg.get("session_id")
        self._agent = msg.get("agent")
        self._capabilities = {
            "requested": msg.get("requested_capabilities", {}) or {},
            "live": msg.get("live_capabilities", {}) or {},
        }
        log.info(
            "Gateway listo (session=%s agent=%s live=%s)",
            self._session_id, self._agent, self._capabilities.get("live"),
        )
        self._publish("gateway_ready", {
            "session_id": self._session_id,
            "agent": self._agent,
            "capabilities": self._capabilities,
        })

    def _publish(self, type_: "EventType", data: dict) -> None:
        if self._bus is None:
            return
        from domain.event_bus import VoiceEvent
        self._bus.publish(VoiceEvent(type=type_, data=data))

    async def disconnect(self) -> None:
        if self._ws:
            ws, self._ws = self._ws, None
            self._closed = True
            try:
                await ws.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Envío
    # ------------------------------------------------------------------

    async def _send(self, payload: Any) -> None:
        if self._ws is None:
            raise RuntimeError("GatewayClient: no conectado")
        if self._closed:
            raise self.last_close or GatewayDisconnected(None)
        try:
            await self._ws.send(payload)
        except websockets.exceptions.ConnectionClosed as exc:
            raise self._mark_closed(*_closed_info(exc)) from exc
        self._last_send_at = time.monotonic()

    async def send_audio(self, float32_bytes: bytes) -> None:
        """Envía un frame de micrófono tal cual: PCM Float32 LE, 16 kHz, mono.

        Asimetría deliberada con la bajada (issue #84): el TTS llega como
        ``[0xA1][turn_seq uint16 BE][PCM16 24 kHz]`` (client-protocol.md §6)
        y la subida va SIN cabecera ni turn_seq (§3 "Header: ninguno", §12
        "bytes PCM Float32 16 kHz sin header"). Es lo que exige el gateway:
        una cabecera se interpretaría como muestras de audio. Tampoco hace
        falta correlación de turno en la subida, ni con sesión persistente:
        el audio del micrófono existe antes de que haya turno (el gateway
        asigna turn_seq en turn_start, tras ``send``) y el barge-in se
        resuelve con ``interrupted`` y el turn_seq de la bajada (§7).
        """
        await self._send(float32_bytes)

    async def send_end(self) -> None:
        await self._send(json.dumps({"type": "end"}))
        log.debug("Gateway: enviado end")

    async def send_cancel(self) -> None:
        # Marcar antes de enviar: aunque el envío falle, el audio del turno
        # actual ya no debe reproducirse.
        self._discard_upto = max(self._discard_upto, self._current_turn_seq)
        await self._send(json.dumps({"type": "cancel"}))
        log.debug("Gateway: enviado cancel")

    async def send_text(self, text: str) -> None:
        """Envía la transcripción confirmada al gateway para disparar el orquestador."""
        await self._send(json.dumps({"type": "send", "text": text}))
        log.debug("Gateway: enviado send %r", text[:60])

    async def send_keepalive(self) -> None:
        """Keepalive de aplicación para el IDLE_TIMEOUT_S del gateway (#116).

        El gateway cuenta como actividad cualquier mensaje entrante del
        cliente (bridge.py `_client_input_loop` actualiza
        `_last_client_activity` antes de mirar el tipo) e ignora los tipos
        JSON que no conoce. Los ping de protocolo no sirven: los contesta
        uvicorn por debajo de la app y no llegan a ese bucle.
        """
        await self._send(json.dumps({"type": "keepalive"}))
        log.debug("Gateway: enviado keepalive")

    # ------------------------------------------------------------------
    # Recepción
    # ------------------------------------------------------------------

    async def receive(self) -> AsyncGenerator[GatewayEvent, None]:
        if self._ws is None or self._closed:
            raise self.last_close or GatewayDisconnected(None, "no conectado")
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
                    if frame_seq < self._current_turn_seq or (
                        self._discard_upto and frame_seq <= self._discard_upto
                    ):
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
                # Revisión Fase R: turn_end se entrega como evento (antes
                # terminaba el generador). Con sesión persistente puede ser el
                # fin de un push ajeno, y tras el nuestro aún llega audio
                # (client-protocol §5): decide _responding, que conoce su turno.
                if event_type == "token":
                    event_type = "llm_token"
                    # El protocolo actual manda el texto en `text`
                    # (client-protocol.md §5); el consumidor lee `content`
                    # (formato anterior a gateway v1.8, issue #2).
                    if "content" not in data:
                        data = {**data, "content": data.get("text", "")}
                yield GatewayEvent(type=event_type, data=data)
        except websockets.exceptions.ConnectionClosed as exc:
            # Issue #118: CUALQUIER cierre (también 1000/1001, que websockets
            # trata como ConnectionClosedOK) se propaga como
            # GatewayDisconnected. El gateway nunca cierra la sesión al
            # terminar un turno (el fin de turno es turn_end), así que un
            # cierre limpio mientras se lee es inactividad (IDLE_TIMEOUT_S),
            # reinicio o despliegue: el turno en curso no terminó bien y la
            # sesión hay que reabrirla. Antes el OK se tragaba en silencio,
            # el turno acababa con playback_ended y el socket muerto seguía
            # figurando como conectado.
            raise self._mark_closed(*_closed_info(exc)) from exc
        # El iterador de websockets termina sin excepción cuando la conexión
        # se cierra con 1000/1001 (su __aiter__ se traga ConnectionClosedOK).
        # Mismo tratamiento: es un cierre, no un fin de turno. Un iterable
        # sin close_code (fakes de test) que se agota termina en silencio.
        ws = self._ws
        code = getattr(ws, "close_code", None) if ws is not None else None
        if isinstance(code, int):
            reason = getattr(ws, "close_reason", "")
            raise self._mark_closed(code, reason if isinstance(reason, str) else "")
