"""
state_machine.py — Máquina de estados principal de jota-voice v2.

Coordina AudioCapture, OWWClient, GatewayClient y PlaybackEngine.
Toda la lógica de negocio vive aquí; no hace I/O directamente.

Estados: IDLE → RECORDING → RESPONDING → IDLE

OWW corre como task background persistente (ver oww_client.run_forever):
- Publica VoiceEvent(type="wake_word_detected") en el bus cuando detecta
- IDLE consume ese evento del bus para empezar nuevo turn
- RECORDING/RESPONDING monitorizan el bus y cancelan el turn actual si llega
  otro wake_word (wake word interrumpe TTS, estilo Alexa/Google)

Publica en EventBus en cada transición y en cada evento relevante.
Toda transición de error publica VoiceEvent(type="error") antes de volver a IDLE.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import numpy as np

from backends.audio_base import AudioBackend
from backends.gateway_client import GatewayClient
from config import Config

from .event_bus import EventBus, VoiceEvent
from app.playback_engine import PlaybackEngine

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

class _TurnCancelled(Exception):
    """Lanzada cuando cancel_event gana la race en RECORDING o RESPONDING."""


class _GatewayError(Exception):
    """Error reportado por el gateway mid-turno (issue #18).

    El orquestador caído emite `{"type":"error","code":"TURN_ERROR",
    "message":"...","fatal":false}` y deja de mandar más eventos para ese
    turno. Antes del fix, _receive_loop() lo tragaba en el `else` final
    (solo log.debug) y el cliente esperaba los 30s del timeout externo
    para reportar el genérico "Timeout en estado RESPONDING" — perdiendo
    el code/message que el servidor ya había dado.

    Levantar esta excepción desde _receive_loop() hace que
    receive_task.exception() la propague al dispatcher externo, que la
    re-raise, que run() la capture y _log_error() publique los campos
    code/message/fatal directamente al bus (no envueltos en str(exc)).
    """

    def __init__(self, code: str, message: str, fatal: object) -> None:
        self.code = code
        self.message = message
        # El protocolo del gateway define fatal como JSON boolean. Aceptar
        # cualquier valor truthy (p.ej. bool("false") == True en Python) haría
        # que strings no-vacíos y enteros disparasen el camino de fatal=True
        # sin ser realmente fatales — false positivo en la severidad del
        # log y en el payload publicado al bus. Solo True/False literales
        # cuentan como boolean; cualquier otra cosa cae al camino no-fatal.
        self.fatal = fatal if isinstance(fatal, bool) else False
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


async def _safe_send_cancel(gateway: GatewayClient) -> None:
    try:
        await gateway.send_cancel()
    except Exception:
        pass

async def _idle(
    cfg: Config,
    bus: EventBus,
    audio: AudioBackend,
    cancel_event: asyncio.Event,
) -> str:
    """
    Estado IDLE: espera wake_word_detected del bus y devuelve el nombre.

    OWW corre como task background persistente (oww_client.run_forever) y
    publica wake_word_detected en el bus cuando detecta. IDLE consume ese
    evento. Esto permite que OWW siga escuchando durante RECORDING/RESPONDING
    y pueda interrumpir el turno actual.

    Limpia el cancel_event por si quedó seteado de un /cancel externo o de
    una wake_word recibida mientras estábamos en otro estado.
    """
    q = audio.get_queue()

    # 1. Drenar audio stale para evitar que frames pre-wake contaminen la captura
    drained = 0
    while not q.empty():
        q.get_nowait()
        drained += 1
    if drained:
        log.debug("IDLE: descartados %d frames stale", drained)

    # 2. Limpiar cualquier cancel pendiente
    cancel_event.clear()

    # 3. Publicar state_changed("idle")
    bus.publish(VoiceEvent(type="state_changed", data={"state": "idle"}))
    log.info("IDLE: esperando wake_word del bus…")

    # 4. Suscribirse al bus y esperar primer wake_word_detected
    timeout_s = cfg.oww.idle_detection_timeout_s
    if timeout_s > 0:
        # Aplicar timeout: si no llega wake_word_detected en `timeout_s`,
        # lanzar OSError para que run() publique error y vuelva a IDLE.
        async def _wait_wake_or_timeout() -> str:
            try:
                async for event in bus.subscribe():
                    if event.type == "wake_word_detected":
                        return event.data.get("wake_word", "")
            except asyncio.CancelledError:
                raise
        try:
            return await asyncio.wait_for(_wait_wake_or_timeout(), timeout=timeout_s)
        except asyncio.TimeoutError:
            raise OSError(
                f"IDLE: timeout {timeout_s}s esperando wake_word_detected"
            )
    else:
        async for event in bus.subscribe():
            if event.type == "wake_word_detected":
                wake_word = event.data.get("wake_word", "")
                log.info("IDLE: wake word recibido → %r", wake_word)
                return wake_word


async def _wait_wake_or_cancel(
    bus: EventBus,
    cancel_event: asyncio.Event,
    current_state: str,
) -> None:
    """
    Bloquea hasta que llegue wake_word_detected al bus o se setee cancel_event.

    Usado en RECORDING y RESPONDING para implementar wake-word-interrumpe-TTS:
    si el usuario dice la wake word mientras jota-voice está grabando o
    reproduciendo respuesta, esta coroutine retorna, el caller lanza
    _TurnCancelled, y el state_machine vuelve a IDLE que ya tiene el
    wake_word en el bus listo para consumir.
    """
    cancel_task = asyncio.create_task(cancel_event.wait())
    wake_task = asyncio.create_task(_consume_wake(bus))
    try:
        done, pending = await asyncio.wait(
            [cancel_task, wake_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        if wake_task in done and not wake_task.cancelled():
            log.info("%s: wake_word detectado durante el estado, interrumpiendo", current_state)
            cancel_event.set()  # para que el state_machine vea _TurnCancelled
    finally:
        for t in (cancel_task, wake_task):
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass


async def _consume_wake(bus: EventBus) -> str:
    """Lee el bus hasta encontrar wake_word_detected."""
    async for event in bus.subscribe():
        if event.type == "wake_word_detected":
            return event.data.get("wake_word", "")


async def _recording(
    wake_word: str,
    bus: EventBus,
    audio: AudioBackend,
    gateway: GatewayClient,
    playback: PlaybackEngine,
    cfg: Config,
    cancel_event: asyncio.Event,
) -> None:
    cancel_event.clear()

    bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": wake_word}))
    bus.publish(VoiceEvent(type="recording_started", data={}))

    # Descarta el backlog acumulado en get_queue() mientras _idle() esperaba
    # la wake word: desde el fan-out de audio (issue #9), esa cola ya no
    # tiene ningún consumidor durante esa espera (antes, OWWClient la
    # drenaba como efecto secundario de compartir la misma cola). Sin este
    # descarte, _capture_loop consumiría ese ruido de ambiente como si fuera
    # la voz del usuario y podría agotar silence_frames_needed antes de
    # llegar al audio real — cortando el turno sin haber oído nada nuevo.
    # get_preroll() ya cubre el contexto de los últimos segundos antes de la
    # wake word. Se descarta AQUÍ (antes de connect()/preroll), no dentro de
    # _capture_loop(): si se descartara después, se perderían también los
    # frames legítimos capturados durante el propio connect() al gateway.
    q = audio.get_queue()
    drained = 0
    while not q.empty():
        q.get_nowait()
        drained += 1
    if drained:
        log.debug("RECORDING: descartados %d frames stale de la cola", drained)

    await asyncio.wait_for(
        gateway.connect(),
        timeout=cfg.gateway.connect_timeout_s,
    )
    log.debug("RECORDING: gateway conectado")

    preroll = audio.get_preroll()
    if preroll:
        await gateway.send_audio(preroll)
        log.debug("RECORDING: pre-roll enviado (%d bytes)", len(preroll))

    async def _capture_loop() -> None:
        q = audio.get_queue()
        silence_frames_needed = max(1, int(
            cfg.audio.silence_timeout_s * cfg.audio.sample_rate / cfg.audio.frames_per_buffer
        ))
        silence_count = 0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + cfg.audio.recording_timeout_s

        while loop.time() < deadline:
            remaining = deadline - loop.time()
            try:
                frame = await asyncio.wait_for(q.get(), timeout=min(remaining, 0.1))
            except asyncio.TimeoutError:
                continue
            await gateway.send_audio(frame)
            if audio.is_silence(frame):
                silence_count += 1
                if silence_count >= silence_frames_needed:
                    log.info("RECORDING: fin por silencio (%d frames)", silence_count)
                    return
            else:
                silence_count = 0
        log.info("RECORDING: timeout absoluto alcanzado (%.1fs)", cfg.audio.recording_timeout_s)

    capture_task = asyncio.create_task(_capture_loop())
    cancel_task = asyncio.create_task(cancel_event.wait())
    # RECORDING: NO monitorizamos wake_word — durante la grabación estamos
    # escuchando al usuario y su propia voz produciría falsos positivos. El
    # wake_word solo puede interrumpir durante RESPONDING (TTS sonando).

    try:
        done, pending = await asyncio.wait(
            [capture_task, cancel_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        if cancel_task in done:
            await _safe_send_cancel(gateway)
            raise _TurnCancelled()

        if not capture_task.cancelled():
            exc = capture_task.exception()
            if exc is not None:
                raise exc
    finally:
        # Issue #21: si la tarea externa fue cancelada durante el wait
        # (SIGTERM durante shutdown), el código de arriba no se ejecuta
        # pero los tasks hijos sí. Drenarlos siempre, replicando el patrón
        # de _wait_wake_or_cancel (líneas 175-181).
        for t in (capture_task, cancel_task):
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    # play_notification() es un beep puramente cosmético (confirma al
    # usuario que se dejó de grabar). Un fallo de audio de salida (visto en
    # producción: dispositivo Bluetooth intermitente, PaErrorCode -9986) no
    # debe impedir gateway.send_end() — sin él, el gateway se queda sin
    # saber que el turno terminó y el error se propaga más tarde como un
    # fallo de conexión aparentemente no relacionado.
    try:
        await playback.play_notification()
    except Exception as exc:
        log.warning("RECORDING: fallo al reproducir notificación (no crítico): %s", exc)
        bus.publish(VoiceEvent(type="error", data={"message": f"Notificación de audio falló: {exc}"}))

    await gateway.send_end()
    bus.publish(VoiceEvent(type="recording_ended", data={}))
    log.debug("RECORDING: end enviado")


async def _responding(
    bus: EventBus,
    gateway: GatewayClient,
    playback: PlaybackEngine,
    cancel_event: asyncio.Event,
) -> None:
    # Limpiar cualquier cancel pendiente del turn anterior o de /cancel
    # recibido fuera de contexto — si quedó seteado, el wait() siguiente
    # completaría inmediatamente y abortaría el turn sin reproducir respuesta.
    cancel_event.clear()
    playback_started = False

    async def _receive_loop() -> None:
        nonlocal playback_started
        # El protocolo de jota-gateway (docs/client-protocol.md) señala fin
        # de turno con "turn_end", nunca con "done" — y no cierra la
        # conexión WS tras el turno (puede haber más turnos en la misma
        # sesión). Sin este margen, el loop dependería siempre del timeout
        # externo de 30s para cortar, aunque la respuesta ya haya terminado.
        TURN_END_GRACE_S = 2.0
        gen = gateway.receive()
        loop = asyncio.get_running_loop()
        grace_deadline: Optional[float] = None

        while True:
            try:
                if grace_deadline is not None:
                    remaining = grace_deadline - loop.time()
                    if remaining <= 0:
                        return
                    gw_event = await asyncio.wait_for(gen.__anext__(), timeout=remaining)
                else:
                    gw_event = await gen.__anext__()
            except asyncio.TimeoutError:
                return
            except StopAsyncIteration:
                return

            if gw_event.type == "transcription":
                text = gw_event.data.get("text", "")
                bus.publish(VoiceEvent(type="transcription", data={"text": text}))
                log.info("RESPONDING: transcription → %r", text)
                await gateway.send_text(text)

            elif gw_event.type == "transcription_partial":
                text = gw_event.data.get("text", "")
                bus.publish(VoiceEvent(type="transcription_partial", data={"text": text}))

            elif gw_event.type == "llm_token":
                content = gw_event.data.get("content", "")
                playback.push_token(content)
                bus.publish(VoiceEvent(type="llm_token", data={"content": content}))

            elif gw_event.type == "tts_chunk":
                if not playback_started:
                    bus.publish(VoiceEvent(type="playback_started", data={}))
                    playback_started = True
                audio_bytes = gw_event.data.get("audio", b"")
                await playback.play_chunk(audio_bytes)
                if grace_deadline is not None:
                    # Sigue llegando audio en tránsito tras turn_end — no cortar aún.
                    grace_deadline = loop.time() + TURN_END_GRACE_S

            elif gw_event.type == "turn_end":
                grace_deadline = loop.time() + TURN_END_GRACE_S

            elif gw_event.type == "status":
                # Issue #18 — informativo: el gateway reporta degradación
                # de orchestrator/transcriber/tts mid-turno sin matar la
                # sesión. Antes del fix caía en el else con log.debug,
                # invisible para la UI (TTS degradado, fallback engine…).
                bus.publish(VoiceEvent(type="gateway_status", data=gw_event.data))
                log.info("RESPONDING: status de gateway → %r", gw_event.data)

            elif gw_event.type == "error":
                # Issue #18 — propagar AHORA el error del gateway con su
                # code/message/fatal, en vez de esperar al timeout externo
                # de 30s (que reportaría el genérico "Timeout en estado
                # RESPONDING") o devolver en silencio (que publicaría
                # playback_ended como si el turno hubiera terminado bien).
                # No publicamos playback_ended ni playback.drain() — el
                # turno termina con error, no con éxito.
                raise _GatewayError(
                    code=str(gw_event.data.get("code", "")),
                    message=str(gw_event.data.get("message", "")),
                    fatal=gw_event.data.get("fatal", False),
                )

            else:
                log.debug("RESPONDING: evento desconocido de gateway: %r", gw_event.type)

    receive_task = asyncio.create_task(
        asyncio.wait_for(_receive_loop(), timeout=30.0)
    )
    cancel_task = asyncio.create_task(cancel_event.wait())
    wake_task = asyncio.create_task(_wait_wake_or_cancel(bus, cancel_event, "RESPONDING"))

    try:
        done, pending = await asyncio.wait(
            [receive_task, cancel_task, wake_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        if cancel_task in done:
            await _safe_send_cancel(gateway)
            playback.reset()
            raise _TurnCancelled()

        if not receive_task.cancelled():
            exc = receive_task.exception()
            if exc is not None:
                if isinstance(exc, asyncio.TimeoutError):
                    log.warning("RESPONDING: timeout 30s")
                    bus.publish(VoiceEvent(type="error", data={"message": "Timeout en estado RESPONDING"}))
                    return
                raise exc
    finally:
        # Issue #21: si la tarea externa fue cancelada durante el wait
        # (SIGTERM durante shutdown), el código de arriba no se ejecuta
        # pero los tasks hijos sí. Drenarlos siempre, replicando el patrón
        # de _wait_wake_or_cancel (líneas 175-181) y _recording.
        for t in (receive_task, cancel_task, wake_task):
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    await playback.drain()
    bus.publish(VoiceEvent(type="playback_ended", data={}))
    log.debug("RESPONDING: reproducción completada")


# ---------------------------------------------------------------------------
# Helpers de error handling
# ---------------------------------------------------------------------------

def _log_error(state: str, exc: Exception, bus: EventBus) -> None:
    # Issue #18 — los errores del gateway llegan con code/message/fatal
    # estructurados. Publicarlos directamente al bus (no envueltos en
    # str(exc)) permite que la UI los muestre literalmente y que un
    # orquestador caído (TURN_ERROR) no aparezca como el genérico
    # "Timeout en estado RESPONDING".
    if isinstance(exc, _GatewayError):
        level = log.error if exc.fatal else log.warning
        level(
            "Error en estado %s: gateway code=%r message=%r fatal=%s",
            state, exc.code, exc.message, exc.fatal,
        )
        bus.publish(VoiceEvent(type="error", data={
            "code": exc.code,
            "message": exc.message,
            "fatal": exc.fatal,
        }))
        return
    log.error("Error en estado %s: %s", state, exc)
    bus.publish(VoiceEvent(type="error", data={"message": str(exc)}))


def _log_cancelled(state: str, bus: EventBus) -> None:
    """Sin esto, un turn cancelado (nueva wake word interrumpiendo TTS, o
    /cancel externo vía ControlServer) es invisible para cualquier UI: no
    hay error, no hay un nuevo "listening" garantizado, solo silencio hasta
    que _idle() vuelve a publicar "idle" en la siguiente iteración."""
    log.info("StateMachine: turn cancelado en %s", state)
    bus.publish(VoiceEvent(type="cancelled", data={"state": state}))


async def _cleanup(gateway: GatewayClient, playback: PlaybackEngine) -> None:
    try:
        await gateway.disconnect()
    except Exception:
        pass
    playback.reset()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run(
    cfg: Config,
    bus: EventBus,
    audio: AudioBackend,
    gateway: GatewayClient,
    playback: PlaybackEngine,
    cancel_event: Optional[asyncio.Event] = None,
) -> None:
    if cancel_event is None:
        cancel_event = asyncio.Event()

    log.info("StateMachine: iniciando loop")

    while True:
        state = "IDLE"
        try:
            wake_word = await _idle(cfg, bus, audio, cancel_event)
        except asyncio.CancelledError:
            log.info("StateMachine: cancelado en IDLE")
            raise
        except Exception as exc:
            _log_error(state, exc, bus)
            await _cleanup(gateway, playback)
            continue

        state = "RECORDING"
        try:
            await _recording(wake_word, bus, audio, gateway, playback, cfg, cancel_event)
        except _TurnCancelled:
            _log_cancelled(state, bus)
            await _cleanup(gateway, playback)
            continue
        except asyncio.CancelledError:
            log.info("StateMachine: cancelado en RECORDING")
            await _cleanup(gateway, playback)
            raise
        except Exception as exc:
            _log_error(state, exc, bus)
            await _cleanup(gateway, playback)
            continue

        state = "RESPONDING"
        try:
            await _responding(bus, gateway, playback, cancel_event)
        except _TurnCancelled:
            _log_cancelled(state, bus)
        except asyncio.CancelledError:
            log.info("StateMachine: cancelado en RESPONDING")
            raise
        except Exception as exc:
            _log_error(state, exc, bus)
        finally:
            await _cleanup(gateway, playback)
