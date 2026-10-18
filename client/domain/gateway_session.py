"""gateway_session.py — sesión persistente con el gateway durante IDLE.

Issue #118 (y #116): en modo persistente la sesión WebSocket se abre una
vez, pero el gateway la cierra por inactividad (1000 a los 300 s), al
reiniciarse (1001/1012), por cliente lento (1013) o se corta la red (1006).
Antes nadie la reabría y el cliente quedaba inservible hasta reiniciar.

Reparto de responsabilidades (un único lector del socket en cada momento):

- IDLE: ``idle_supervisor`` es el único que lee ``gateway.receive()``. Lo
  arranca ``state_machine._idle()`` y lo cancela en cuanto llega la wake
  word, ANTES de que RECORDING/RESPONDING lean. Sustituye al antiguo
  ``_idle_coordinator`` de voice_client, que decidía por eventos del bus y
  podía dejar dos lectores a la vez (se re-armaba con cualquier ``error``,
  también a mitad de turno) o ninguno (moría al acabar un push).
- Turno: ``state_machine._recording()`` reconecta de forma perezosa si la
  sesión está caída al empezar (``gateway.ensure_connected()``); un cierre a
  mitad de turno termina el turno con error visible y el siguiente IDLE
  reabre la sesión.

El supervisor:
  1. Si la sesión está caída, reconecta con backoff (``reconnect_backoff_s``,
     el último valor se repite: backoff con máximo). Tras un cierre 1000
     (inactividad, cierre ordenado) el primer reintento es inmediato.
  2. Si el último rechazo fue fatal (1008 de auth/política, HTTP 401/403) NO
     reintenta en segundo plano: lo publica una vez como ``error`` y espera.
     Solo un turno pedido por el usuario vuelve a intentarlo (visible).
  3. Con la sesión viva, escucha ``status`` (→ bus) y descarta lo demás; y
     manda un keepalive de aplicación si el cliente lleva
     ``keepalive_interval_s`` sin enviar nada, para que el gateway no cierre
     por IDLE_TIMEOUT_S.
"""

from __future__ import annotations

import asyncio
import logging

from backends.gateway_client import (
    GatewayClient,
    GatewayDisconnected,
    GatewayHandshakeError,
)
from config import GatewayConfig
from core.aio import cancel_and_wait

from .event_bus import EventBus, VoiceEvent

log = logging.getLogger(__name__)

# Espera mínima entre dos escuchas consecutivas: receive() termina al ver un
# turn_end (p.ej. de un push del agente) y se vuelve a llamar. Si terminara
# al instante en bucle (socket raro, mock), esto evita girar en caliente.
_MIN_RELISTEN_S = 0.05
# Una sesión que dura menos que esto antes de cerrarse no reinicia el
# backoff: si el gateway nos cierra justo tras abrir, se espera cada vez más.
_STABLE_SESSION_S = 10.0


def _status(bus: EventBus, state: str, **extra) -> None:
    bus.publish(VoiceEvent(
        type="gateway_status",
        data={"service": "gateway", "state": state, **extra},
    ))


def handshake_error_event(exc: GatewayHandshakeError) -> VoiceEvent:
    return VoiceEvent(type="error", data={
        "code": "GATEWAY_HANDSHAKE_REJECTED",
        "kind": exc.kind,
        "close_code": exc.code,
        "message": str(exc),
        "fatal": exc.fatal,
    })


async def gateway_idle_listener(bus: EventBus, gateway: GatewayClient) -> str:
    """Consume UN generador de ``gateway.receive()`` durante IDLE.

    Devuelve por qué terminó: ``"ended"`` (turn_end de un push o fin del
    iterador), ``"closed"`` (cierre de sesión, ya publicado como
    ``gateway_status`` disconnected) o ``"failed"`` (excepción inesperada,
    ya publicada como ``error``).
    """
    try:
        async for ev in gateway.receive():
            if ev.type == "status":
                bus.publish(VoiceEvent(type="gateway_status", data=ev.data))
                log.info("IDLE: gateway status → %r", ev.data)
            elif ev.type in ("interrupted", "turn_start", "tool_call"):
                # Los push del agente durante IDLE no se reproducen todavía
                # (fuera del alcance de #24/#118) — log y descartar.
                log.warning("IDLE: gateway evento %r recibido y descartado", ev.type)
            else:
                log.debug("IDLE: gateway evento ignorado %r", ev.type)
        return "ended"
    except GatewayDisconnected as exc:
        log.warning("IDLE: sesión con el gateway cerrada: %s", exc)
        _status(bus, "disconnected", code=exc.code, reason=exc.reason)
        return "closed"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.exception("IDLE: el listener del gateway terminó con excepción")
        bus.publish(VoiceEvent(type="error", data={"message": str(exc)}))
        return "failed"


async def _keepalive_loop(gateway: GatewayClient, interval_s: float) -> None:
    """Envía keepalive cuando el cliente lleva ``interval_s`` sin enviar."""
    while True:
        wait = interval_s - gateway.seconds_since_last_send
        if wait > 0:
            await asyncio.sleep(wait)
            continue
        await gateway.send_keepalive()


async def _listen_connected(
    cfg: GatewayConfig, bus: EventBus, gateway: GatewayClient
) -> str:
    """Escucha mientras la sesión siga viva; keepalive en paralelo."""
    listener = asyncio.create_task(gateway_idle_listener(bus, gateway))
    tasks: list[asyncio.Task] = [listener]
    if cfg.keepalive_interval_s > 0:
        tasks.append(asyncio.create_task(
            _keepalive_loop(gateway, cfg.keepalive_interval_s)
        ))
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        await cancel_and_wait(*tasks)
    if listener.done() and not listener.cancelled() and listener.exception() is None:
        return listener.result()
    # Terminó el keepalive: el envío falló (sesión cerrada) o error inesperado.
    for t in tasks[1:]:
        if t.done() and not t.cancelled() and t.exception() is not None:
            exc = t.exception()
            if isinstance(exc, GatewayDisconnected):
                _status(bus, "disconnected", code=exc.code, reason=exc.reason)
                return "closed"
            log.warning("IDLE: keepalive falló: %s", exc)
            return "failed"
    return "failed"


async def idle_supervisor(
    cfg: GatewayConfig,
    bus: EventBus,
    gateway: GatewayClient,
) -> None:
    """Mantiene viva la sesión persistente mientras el estado es IDLE.

    Corre hasta que lo cancelan (wake word). Ver docstring del módulo.
    """
    backoff = list(cfg.reconnect_backoff_s) or [1.0]
    attempt = 0
    failures = 0  # fallos de conexión seguidos (para avisar solo del primero)
    reported_fatal: GatewayHandshakeError | None = None
    loop = asyncio.get_running_loop()
    listen_started = loop.time()
    while True:
        if gateway.is_connected:
            reason = await _listen_connected(cfg, bus, gateway)
            if reason == "failed":
                # Error inesperado leyendo con la sesión aparentemente viva:
                # no relanzar en caliente (evita el bucle de errores de #118).
                await asyncio.sleep(backoff[min(attempt, len(backoff) - 1)])
                attempt += 1
            elif reason == "closed":
                last = gateway.last_close
                lived = loop.time() - listen_started
                if last is not None and last.code == 1000 and lived >= _STABLE_SESSION_S:
                    # 1000 tras una sesión estable = inactividad o cierre
                    # ordenado: reabrir ya, sin esperar.
                    attempt = 0
                elif lived >= _STABLE_SESSION_S:
                    attempt = 1
                else:
                    # La sesión murió nada más abrirse: escalar el backoff
                    # para no martillear un gateway que nos cierra en bucle.
                    attempt += 1
            else:
                await asyncio.sleep(_MIN_RELISTEN_S)
            continue

        fatal = gateway.fatal_error
        if fatal is not None or (gateway.last_close is not None and gateway.last_close.fatal):
            # Cierre/rechazo fatal (1008): no reintentar en bucle. Se publica
            # una vez y se espera; el próximo turno lo reintentará (visible).
            if fatal is not None and fatal is not reported_fatal:
                reported_fatal = fatal
                log.error("Gateway: rechazo fatal, sin reintentos automáticos: %s", fatal)
                bus.publish(handshake_error_event(fatal))
            elif fatal is None and reported_fatal is None:
                reported_fatal = GatewayHandshakeError("policy_rejected", 1008, gateway.last_close.reason)
                log.error("Gateway: sesión cerrada con 1008, sin reintentos automáticos")
                bus.publish(handshake_error_event(reported_fatal))
            await asyncio.Event().wait()

        delay = 0.0 if attempt == 0 else backoff[min(attempt - 1, len(backoff) - 1)]
        if delay > 0:
            _status(bus, "reconnecting", attempt=attempt, delay_s=delay)
            log.info("Gateway: reintento de conexión %d en %.1fs", attempt, delay)
            await asyncio.sleep(delay)
        try:
            await gateway.ensure_connected()
        except GatewayHandshakeError as exc:
            attempt += 1
            failures += 1
            if exc.fatal:
                continue  # la rama de arriba lo publica y se queda esperando
            log.warning("Gateway: reconexión rechazada (%s); se reintentará", exc)
            if failures == 1:
                bus.publish(handshake_error_event(exc))
            continue
        except (OSError, asyncio.TimeoutError, Exception) as exc:  # noqa: B014
            attempt += 1
            failures += 1
            log.warning("Gateway: reconexión fallida (intento %d): %s", attempt, exc)
            if failures == 1:
                # Solo el primer fallo de la racha va como `error` (visible en
                # menubar); el resto como gateway_status para no inundar.
                bus.publish(VoiceEvent(type="error", data={
                    "code": "GATEWAY_DISCONNECTED",
                    "message": f"No se pudo reconectar con el gateway: {exc}",
                    "fatal": False,
                }))
            continue
        reported_fatal = None
        failures = 0
        listen_started = loop.time()
        log.info("Gateway: sesión reabierta (session=%s)", gateway._session_id)
        _status(bus, "connected", session_id=gateway._session_id)
