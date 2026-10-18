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
import contextlib
import logging

from app.playback_engine import PlaybackEngine
from backends.audio_base import AudioBackend
from collections.abc import Awaitable, Callable

from backends.gateway_client import (
    GatewayClient,
    GatewayDisconnected,
    GatewayHandshakeError,
)
from config import Config, GatewayConfig
from core.aio import cancel_and_wait

from .event_bus import EventBus, VoiceEvent

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

# Los plazos de RESPONDING viven en GatewayConfig (issue #65):
# turn_end_grace_s, response_start_timeout_s y turn_inactivity_timeout_s.


class _ResponseDeadline:
    """Plazo de INACTIVIDAD de RESPONDING (issue #65).

    Antes era un tope total (``wait_for(_receive_loop(), 30)``): cualquier
    respuesta de más de 30 s se cortaba aunque siguiera llegando audio, y sin
    avisar al gateway. Ahora, como el TURN_TIMEOUT_S del gateway
    (client-protocol §13, "idle-reset"), el reloj se reinicia con cada señal
    de progreso de NUESTRO turno. Dos fases:

    - Hasta que empieza nuestro turno (transcripción, turn_start):
      ``response_start_timeout_s``. El gateway no acota esta espera (si el
      transcriptor no entrega nada, p. ej. una falsa wake word sin voz,
      nadie avisaría), así que aquí manda el cliente.
    - Con nuestro turno en marcha: ``turn_inactivity_timeout_s``. Por
      defecto por encima de los 120 s del TURN_TIMEOUT_S del gateway: si el
      orquestador se cuelga, llega antes su ``TURN_ERROR turn_timeout``, que
      es más preciso; y mientras una herramienta trabaja el cliente no ve
      tráfico (no pedimos tool_calls_enabled) aunque el gateway siga vivo.
      El plazo del cliente queda como red de seguridad.

    ``clock`` inyectable para testear el reinicio sin esperas reales.
    """

    def __init__(self, start_timeout_s: float, turn_timeout_s: float, clock) -> None:
        self._start = start_timeout_s
        self._turn = turn_timeout_s
        self._clock = clock
        self.phase = "start"
        self._deadline = clock() + start_timeout_s

    @property
    def timeout_s(self) -> float:
        return self._turn if self.phase == "turn" else self._start

    def progress(self) -> None:
        self._deadline = self._clock() + self.timeout_s

    def our_turn_started(self) -> None:
        self.phase = "turn"
        self.progress()

    def remaining(self) -> float:
        return self._deadline - self._clock()


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

    def __init__(
        self, code: str, message: str, fatal: object, extra: dict | None = None
    ) -> None:
        self.code = code
        self.message = message
        self.extra = dict(extra or {})
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


class _ResponseTimeout(_GatewayError):
    """RESPONDING sin progreso durante su plazo de inactividad (issue #65).

    _responding manda ``cancel`` al gateway antes de propagarla (que deje de
    generar y no siga mandando audio de un turno que el cliente ya abandonó)
    y run() la publica como ``error`` con code RESPONSE_TIMEOUT.
    """

    def __init__(self, *, phase: str, timeout_s: float) -> None:
        if phase == "start":
            what = "no empezó a responder"
        else:
            what = "dejó de responder"
        super().__init__(
            code="RESPONSE_TIMEOUT",
            message=f"El gateway {what} en {timeout_s:g} s; turno cancelado",
            fatal=False,
            extra={"phase": phase, "timeout_s": timeout_s},
        )


async def _safe_send_cancel(gateway: GatewayClient) -> None:
    """Envía ``cancel`` sin dejar que un fallo tumbe la limpieza del turno.

    Issue #66: antes el fallo se tragaba sin rastro. Una sesión ya cerrada es
    lo esperable (cancel tras un corte) y va a DEBUG; cualquier otra cosa es
    un fallo real y va a WARNING.
    """
    try:
        await gateway.send_cancel()
    except GatewayDisconnected as exc:
        log.debug("cancel no enviado: la sesión con el gateway ya está cerrada (%s)", exc)
    except Exception as exc:
        log.warning("cancel no enviado al gateway: %r", exc)


async def _idle(
    cfg: Config,
    bus: EventBus,
    audio: AudioBackend,
    cancel_event: asyncio.Event,
    background: Callable[[], Awaitable[None]] | None = None,
) -> str:
    """Envuelve ``_wait_idle`` con una tarea de fondo que solo vive en IDLE.

    Issue #118: en modo persistente ``background`` es el supervisor de la
    sesión con el gateway (``domain.gateway_session.idle_supervisor``): es el
    único lector del socket mientras se espera la wake word y se cancela
    —esperando a que termine— ANTES de volver, así que RECORDING/RESPONDING
    nunca comparten el socket con él.
    """
    if background is None:
        return await _wait_idle(cfg, bus, audio, cancel_event)
    bg_task = asyncio.create_task(background(), name="gateway_idle_supervisor")
    try:
        return await _wait_idle(cfg, bus, audio, cancel_event)
    finally:
        await cancel_and_wait(bg_task)
        if not bg_task.cancelled() and bg_task.exception() is not None:
            log.error("IDLE: el supervisor del gateway falló: %r", bg_task.exception())


async def _wait_idle(
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

    Issue #62 — el turno empieza en el instante en que se consume la wake
    word: ahí (y solo ahí) se limpia ``cancel_event``. Un cancel anterior (un
    /cancel pulsado en reposo, o el resto de un turno anterior) no tiene turno
    al que aplicar y se descarta; uno posterior pertenece al turno nuevo y
    RECORDING/RESPONDING lo respetan aunque llegue entre estados. Equivale a
    marcar cada cancel con su instante y compararlo con el inicio del turno,
    sin cambiar el tipo de ``cancel_event`` (lo comparten el display y
    voice_client como un asyncio.Event normal) y sin ventana: entre consumir
    la wake word y limpiar no hay ningún await.
    """
    q = audio.get_queue()

    # 1. Drenar audio stale para evitar que frames pre-wake contaminen la captura
    drained = 0
    while not q.empty():
        q.get_nowait()
        drained += 1
    if drained:
        log.debug("IDLE: descartados %d frames stale", drained)

    # 2. Publicar state_changed("idle")
    bus.publish(VoiceEvent(type="state_changed", data={"state": "idle"}))
    log.info("IDLE: esperando wake_word del bus…")

    # 3. Suscribirse al bus y esperar primer wake_word_detected
    timeout_s = cfg.oww.idle_detection_timeout_s
    if timeout_s > 0:
        # Aplicar timeout: si no llega wake_word_detected en `timeout_s`,
        # lanzar OSError para que run() publique error y vuelva a IDLE.
        try:
            wake_word = await asyncio.wait_for(_consume_wake(bus), timeout=timeout_s)
        except asyncio.TimeoutError:
            raise OSError(
                f"IDLE: timeout {timeout_s}s esperando wake_word_detected"
            )
    else:
        wake_word = await _consume_wake(bus)
    # 4. Inicio del turno: descartar los cancel anteriores (ver docstring).
    cancel_event.clear()
    log.info("IDLE: wake word recibido → %r", wake_word)
    return wake_word


async def _consume_wake(bus: EventBus) -> str:
    """Lee el bus hasta encontrar wake_word_detected.

    Issue #92: ``aclosing`` retira la suscripción en el acto al volver (o al
    cancelarse), en vez de depender de que el finalizador de async-generators
    de asyncio lo haga más tarde.
    """
    async with contextlib.aclosing(bus.subscribe()) as events:
        async for event in events:
            if event.type == "wake_word_detected":
                return event.data.get("wake_word", "")
    raise OSError("EventBus cerrado esperando wake_word_detected")


async def _recording(
    wake_word: str,
    bus: EventBus,
    audio: AudioBackend,
    gateway: GatewayClient,
    playback: PlaybackEngine,
    cfg: Config,
    cancel_event: asyncio.Event,
) -> None:
    # Sin cancel_event.clear() (issue #62): lo hace IDLE al consumir la wake
    # word; un cancel posterior es de este turno y debe respetarse.
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

    if cfg.gateway.persistent_session:
        # Modo persistente (issue #118): si la sesión está caída al empezar
        # el turno (cierre por inactividad, reinicio del gateway, corte...),
        # reconectar AHORA de forma perezosa. El plazo (connect_timeout_s
        # para abrir + handshake) lo pone GatewayClient.connect(); no se
        # envuelve en otro wait_for para no recortarlo. El audio que el
        # usuario dice mientras tanto se acumula en la cola y se envía tras
        # el handshake. Si no se puede, error visible.
        if not gateway.is_connected:
            log.warning("RECORDING: sesión con el gateway caída; reconectando")
            try:
                await gateway.ensure_connected()
            except GatewayHandshakeError as exc:
                if exc.fatal:
                    # El turno lo publica (vía _log_error): que el supervisor
                    # de la próxima IDLE no lo repita.
                    gateway.reported_fatal = exc
                raise _GatewayError(
                    code="GATEWAY_HANDSHAKE_REJECTED",
                    message=str(exc),
                    fatal=exc.fatal,
                    extra={"kind": exc.kind, "close_code": exc.code},
                ) from exc
            except Exception as exc:
                raise _GatewayError(
                    code="GATEWAY_DISCONNECTED",
                    message=f"No se pudo reconectar con el gateway: {exc or type(exc).__name__}",
                    fatal=False,
                ) from exc
            log.info("RECORDING: sesión con el gateway reabierta")
    else:
        # Modo efímero: conectamos en cada turno (comportamiento previo).
        try:
            await asyncio.wait_for(
                gateway.connect(),
                timeout=cfg.gateway.connect_timeout_s,
            )
            log.debug("RECORDING: gateway conectado")
        except Exception:
            log.exception("RECORDING: gateway.connect() falló")
            raise

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
            # Issue #23: el check `while loop.time() < deadline` solo se
            # ejecuta al INICIO de cada iteración. Sin este wait_for, un
            # `gateway.send_audio(frame)` bloqueante (red degradada,
            # WebSocket congestionado, tc netem) podría colgar el await
            # indefinidamente aunque el deadline ya haya pasado. Acotar
            # send_audio con el `remaining` del deadline lo convierte en
            # un límite duro: si la red no responde, capturamos el
            # TimeoutError y rompemos el bucle para que _recording
            # continúe con send_end.
            try:
                await asyncio.wait_for(gateway.send_audio(frame), timeout=remaining)
            except asyncio.TimeoutError:
                log.warning(
                    "RECORDING: deadline alcanzado durante send_audio "
                    "(%.1fs excedido)", cfg.audio.recording_timeout_s,
                )
                break
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
        await cancel_and_wait(*pending)

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
        # pero los tasks hijos sí. Drenarlos siempre (mismo patrón que
        # _responding).
        await cancel_and_wait(capture_task, cancel_task)

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
    *,
    gateway_cfg: GatewayConfig | None = None,
    clock: Callable[[], float] | None = None,
) -> None:
    """Estado RESPONDING. ``gateway_cfg`` aporta los plazos (issue #65; None =
    los defaults de GatewayConfig). ``clock``: reloj de los plazos, solo para
    tests (por defecto el del loop)."""
    # Sin cancel_event.clear() (issue #62): un cancel que llegó al terminar
    # RECORDING (enviando `end`, durante el beep) es de este turno y debe
    # cancelarlo; los anteriores al turno ya los descartó IDLE.
    if gateway_cfg is None:
        gateway_cfg = GatewayConfig(client_key="")
    if clock is None:
        clock = asyncio.get_running_loop().time
    grace_s = gateway_cfg.turn_end_grace_s
    playback_started = False

    async def _receive_loop() -> None:
        nonlocal playback_started
        # El protocolo de jota-gateway (docs/client-protocol.md) señala fin
        # de turno con "turn_end", nunca con "done" — y no cierra la
        # conexión WS tras el turno (puede haber más turnos en la misma
        # sesión). Tras NUESTRO turn_end se espera turn_end_grace_s al audio
        # en tránsito y se termina.
        gen = gateway.receive()
        grace_deadline: float | None = None
        # Issue #65: plazo de inactividad (ver _ResponseDeadline). Se
        # reinicia DESPUÉS de procesar cada señal de progreso de nuestro
        # turno: el tiempo que el cliente pasa reproduciendo un chunk no es
        # silencio del gateway.
        deadline = _ResponseDeadline(
            gateway_cfg.response_start_timeout_s, gateway_cfg.turn_inactivity_timeout_s, clock,
        )
        # Revisión Fase R: en sesión persistente pueden llegar mensajes de
        # OTROS turnos (un push del agente que empezó antes de nuestro
        # `send`). Nuestro turno es el del primer turn_start visto después de
        # enviar la transcripción; solo su turn_end lo termina, y los tokens
        # y el audio de otros turnos se ignoran.
        sent_text = False
        my_turn_id: object = None
        my_turn_seq: object = None
        foreign_ids: set = set()
        foreign_seqs: set = set()

        def _is_foreign(turn_id: object = None, turn_seq: object = None) -> bool:
            if turn_id is not None:
                if turn_id in foreign_ids:
                    return True
                if my_turn_id is None or turn_id != my_turn_id:
                    return True
            if turn_seq is not None:
                if turn_seq in foreign_seqs:
                    return True
                if my_turn_seq is None or turn_seq != my_turn_seq:
                    return True
            # Sin identificadores (gateways antiguos, fakes): se acepta.
            return False

        while True:
            in_grace = grace_deadline is not None
            if in_grace:
                remaining = grace_deadline - clock()
                if remaining <= 0:
                    return
            else:
                remaining = max(deadline.remaining(), 0.0)
            try:
                gw_event = await asyncio.wait_for(gen.__anext__(), timeout=remaining)
            except asyncio.TimeoutError:
                if in_grace:
                    return
                log.warning(
                    "RESPONDING: %.1fs sin progreso del gateway (fase %s); se cancela el turno",
                    deadline.timeout_s, deadline.phase,
                )
                raise _ResponseTimeout(phase=deadline.phase, timeout_s=deadline.timeout_s)
            except StopAsyncIteration:
                return

            if gw_event.type == "transcription":
                text = gw_event.data.get("text", "")
                bus.publish(VoiceEvent(type="transcription", data={"text": text}))
                log.info("RESPONDING: transcription → %r", text)
                await gateway.send_text(text)
                sent_text = True
                deadline.progress()

            elif gw_event.type == "transcription_partial":
                text = gw_event.data.get("text", "")
                bus.publish(VoiceEvent(type="transcription_partial", data={"text": text}))
                deadline.progress()

            elif gw_event.type == "llm_token":
                if _is_foreign(turn_id=gw_event.data.get("turn_id")):
                    log.debug("RESPONDING: token de otro turno ignorado")
                    continue
                content = gw_event.data.get("content", "")
                playback.push_token(content)
                bus.publish(VoiceEvent(type="llm_token", data={"content": content}))
                deadline.progress()

            elif gw_event.type == "tts_chunk":
                if _is_foreign(turn_seq=gw_event.data.get("turn_seq")):
                    log.debug("RESPONDING: audio de otro turno ignorado")
                    continue
                if not playback_started:
                    bus.publish(VoiceEvent(type="playback_started", data={}))
                    playback_started = True
                audio_bytes = gw_event.data.get("audio", b"")
                await playback.play_chunk(audio_bytes)
                deadline.progress()
                if grace_deadline is not None:
                    # Sigue llegando audio en tránsito tras turn_end — no cortar aún.
                    grace_deadline = clock() + grace_s

            elif gw_event.type == "turn_start":
                # Issue #24: turn_start del gateway resetea el estado de
                # turno. _current_turn_seq ya está actualizado en GatewayClient.
                # Aquí solo reseteamos la grabación_local (cursor/buffer/playback).
                tid = gw_event.data.get("turn_id")
                tseq = gw_event.data.get("turn_seq")
                if sent_text and my_turn_id is None and my_turn_seq is None:
                    my_turn_id, my_turn_seq = tid, tseq
                    deadline.our_turn_started()
                    log.info("RESPONDING: turn_start → nuestro turno %s seq=%s", tid, tseq)
                    # Si había audio en tránsito de un turno anterior con seq
                    # menor, el descarte ya está hecho en receive(). Aquí
                    # limpiamos el buffer de playback por si acaso.
                    playback.reset()
                    # Protocolo display (docs/display-protocol.md): el display
                    # correlaciona por turn_id lo que sigue. Solo el turno
                    # propio: los push ajenos no se muestran ni se reproducen.
                    bus.publish(VoiceEvent(type="turn_start", data={
                        "turn_id": tid, "turn_seq": tseq,
                    }))
                else:
                    # Push del agente (antes de nuestro send) u otro turno:
                    # sus tokens/audio/turn_end no son nuestros.
                    if tid is not None:
                        foreign_ids.add(tid)
                    if tseq is not None:
                        foreign_seqs.add(tseq)
                    log.info("RESPONDING: turn_start ajeno %s seq=%s ignorado", tid, tseq)

            elif gw_event.type == "interrupted":
                # Issue #24: barge-in real del gateway (no basado en wake
                # word). El gateway detectó transcripción parcial del
                # usuario y canceló el turno actual.
                log.info("RESPONDING: interrupted por gateway, cancelando turno")
                await _safe_send_cancel(gateway)
                raise _TurnCancelled()

            elif gw_event.type == "turn_end":
                tid = gw_event.data.get("turn_id")
                # Solo el turn_end del turno abierto tras nuestro send. Un
                # push que acaba mientras tanto (cuyo turn_start vimos en
                # IDLE o no vimos) ya no corta RESPONDING antes de tiempo.
                ours = (my_turn_id is not None or my_turn_seq is not None) and (
                    tid is None or tid == my_turn_id
                )
                if ours:
                    if grace_deadline is None:
                        grace_deadline = clock() + grace_s
                else:
                    log.info("RESPONDING: turn_end ajeno %r ignorado", tid)

            elif gw_event.type == "status":
                # Issue #18 — informativo: el gateway reporta degradación
                # de orchestrator/transcriber/tts mid-turno sin matar la
                # sesión. Antes del fix caía en el else con log.debug,
                # invisible para la UI (TTS degradado, fallback engine…).
                bus.publish(VoiceEvent(type="gateway_status", data=gw_event.data))
                log.info("RESPONDING: status de gateway → %r", gw_event.data)

            elif gw_event.type == "tool_call":
                # jota-gateway client-protocol §5 (opt-in tool_calls_enabled):
                # phase start/result, name, tool_call_id, args, result,
                # is_error. Se pasa al display para mostrar la ejecución.
                if _is_foreign(turn_id=gw_event.data.get("turn_id")):
                    log.debug("RESPONDING: tool_call de otro turno ignorado")
                    continue
                data = {k: v for k, v in gw_event.data.items() if k != "type"}
                bus.publish(VoiceEvent(type="tool_call", data=data))
                deadline.progress()
                log.info(
                    "RESPONDING: tool_call %s %s",
                    data.get("phase"), data.get("name"),
                )

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

    receive_task = asyncio.create_task(_receive_loop())
    # Dos fuentes de cancelación, cada una esperada en UN solo sitio (issue
    # #93: antes la espera de la wake word creaba otro cancel_event.wait()
    # propio y luego hacía cancel_event.set() para que este lo viera).
    cancel_task = asyncio.create_task(cancel_event.wait())
    wake_task = asyncio.create_task(_consume_wake(bus))

    try:
        done, pending = await asyncio.wait(
            [receive_task, cancel_task, wake_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        # Drenar sin tragarse la cancelación de esta tarea ni las
        # excepciones de los hijos (se leen abajo).
        await cancel_and_wait(*pending)

        # Issue #103: si receive_task terminó con un error real (cierre de la
        # conexión, error del gateway) a la vez que el cancel —en el mismo
        # ciclo o mientras se cancelaba—, ese error manda: antes el cancel
        # ganaba, el error se descartaba y en logs/UI solo quedaba "cancel".
        real_exc: BaseException | None = None
        if receive_task.done() and not receive_task.cancelled():
            exc = receive_task.exception()
            if exc is not None and not isinstance(exc, _TurnCancelled):
                real_exc = exc

        woke = wake_task in done and wake_task.exception() is None
        if wake_task in done and not woke:
            raise wake_task.exception()  # el bus se cerró: no hay turno que seguir

        if real_exc is not None:
            if cancel_task in done or woke:
                log.warning(
                    "RESPONDING: cancel simultáneo con error del gateway; "
                    "se informa el error: %s", real_exc,
                )
            if isinstance(real_exc, _ResponseTimeout):
                # Que el gateway deje de generar y no siga mandando audio de
                # un turno que el cliente ya abandonó (issue #65).
                await _safe_send_cancel(gateway)
            playback.reset()
            raise real_exc

        if cancel_task in done or woke:
            if woke:
                # Wake word interrumpe el TTS (estilo Alexa/Google).
                log.info("RESPONDING: wake_word detectado durante el estado, interrumpiendo")
            await _safe_send_cancel(gateway)
            playback.reset()
            raise _TurnCancelled()

        if not receive_task.cancelled():
            exc = receive_task.exception()
            if exc is not None:
                raise exc
    finally:
        # Issue #21: si la tarea externa fue cancelada durante el wait
        # (SIGTERM durante shutdown), el código de arriba no se ejecuta
        # pero los tasks hijos sí. Drenarlos siempre.
        await cancel_and_wait(receive_task, cancel_task, wake_task)

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
            **exc.extra,
            "code": exc.code,
            "message": exc.message,
            "fatal": exc.fatal,
        }))
        return
    if isinstance(exc, GatewayDisconnected):
        # Issue #118: la sesión cayó a mitad de turno. El turno termina con
        # error visible; el siguiente IDLE/turno reabre la sesión.
        log.warning("Error en estado %s: %s", state, exc)
        bus.publish(VoiceEvent(type="error", data={
            "code": "GATEWAY_DISCONNECTED",
            "message": str(exc),
            "close_code": exc.code,
            "reason": exc.reason,
            "fatal": exc.fatal,
        }))
        return
    log.error("Error en estado %s: %r", state, exc)
    # str() de algunas excepciones (TimeoutError()) es "": que la UI no
    # muestre un error vacío.
    bus.publish(VoiceEvent(type="error", data={"message": str(exc) or type(exc).__name__}))


def _log_cancelled(state: str, bus: EventBus) -> None:
    """Sin esto, un turn cancelado (nueva wake word interrumpiendo TTS, o
    `cancel` de un display vía WsDisplayBackend) es invisible para cualquier UI: no
    hay error, no hay un nuevo "listening" garantizado, solo silencio hasta
    que _idle() vuelve a publicar "idle" en la siguiente iteración."""
    log.info("StateMachine: turn cancelado en %s", state)
    bus.publish(VoiceEvent(type="cancelled", data={"state": state}))


async def _cleanup(cfg: Config, gateway: GatewayClient, playback: PlaybackEngine) -> None:
    """Limpia recursos al final de un turno. En modo persistente
    (issue #24) NO desconecta el gateway — eso lo hace voice_client.main
    en el shutdown final.

    Issue #66: decide por ``cfg`` (antes leía ``gateway._cfg``, un atributo
    privado) y deja rastro si ``disconnect`` falla."""
    if not cfg.gateway.persistent_session:
        try:
            await gateway.disconnect()
        except Exception as exc:
            log.warning("Limpieza del turno: gateway.disconnect() falló: %r", exc)
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
    cancel_event: asyncio.Event | None = None,
    *,
    idle_background: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Bucle principal IDLE → RECORDING → RESPONDING.

    ``idle_background``: fábrica de la tarea que corre solo durante IDLE
    (en modo persistente, el supervisor de la sesión con el gateway; ver
    ``_idle``). None = nada en IDLE (modo efímero y tests).
    """
    if cancel_event is None:
        cancel_event = asyncio.Event()

    log.info("StateMachine: iniciando loop")

    while True:
        state = "IDLE"
        try:
            wake_word = await _idle(cfg, bus, audio, cancel_event, idle_background)
        except asyncio.CancelledError:
            log.info("StateMachine: cancelado en IDLE")
            raise
        except Exception as exc:
            _log_error(state, exc, bus)
            await _cleanup(cfg, gateway, playback)
            continue

        state = "RECORDING"
        try:
            await _recording(wake_word, bus, audio, gateway, playback, cfg, cancel_event)
        except _TurnCancelled:
            _log_cancelled(state, bus)
            await _cleanup(cfg, gateway, playback)
            continue
        except asyncio.CancelledError:
            log.info("StateMachine: cancelado en RECORDING")
            await _cleanup(cfg, gateway, playback)
            raise
        except Exception as exc:
            _log_error(state, exc, bus)
            await _cleanup(cfg, gateway, playback)
            continue

        state = "RESPONDING"
        try:
            await _responding(bus, gateway, playback, cancel_event, gateway_cfg=cfg.gateway)
        except _TurnCancelled:
            _log_cancelled(state, bus)
        except asyncio.CancelledError:
            log.info("StateMachine: cancelado en RESPONDING")
            raise
        except Exception as exc:
            _log_error(state, exc, bus)
        finally:
            await _cleanup(cfg, gateway, playback)
