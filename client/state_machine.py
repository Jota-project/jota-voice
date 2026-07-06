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
from config import Config
from event_bus import EventBus, VoiceEvent
from backends.gateway_client import GatewayClient
from playback_engine import PlaybackEngine

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

class _TurnCancelled(Exception):
    """Lanzada cuando cancel_event gana la race en RECORDING o RESPONDING."""


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

    await playback.play_notification()
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
        async for gw_event in gateway.receive():
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

            else:
                log.debug("RESPONDING: evento desconocido de gateway: %r", gw_event.type)

    receive_task = asyncio.create_task(
        asyncio.wait_for(_receive_loop(), timeout=30.0)
    )
    cancel_task = asyncio.create_task(cancel_event.wait())
    wake_task = asyncio.create_task(_wait_wake_or_cancel(bus, cancel_event, "RESPONDING"))

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

    await playback.drain()
    bus.publish(VoiceEvent(type="playback_ended", data={}))
    log.debug("RESPONDING: reproducción completada")


# ---------------------------------------------------------------------------
# Helpers de error handling
# ---------------------------------------------------------------------------

def _log_error(state: str, exc: Exception, bus: EventBus) -> None:
    log.error("Error en estado %s: %s", state, exc)
    bus.publish(VoiceEvent(type="error", data={"message": str(exc)}))


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
            log.info("StateMachine: turn cancelado en RECORDING")
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
            log.info("StateMachine: turn cancelado en RESPONDING")
        except asyncio.CancelledError:
            log.info("StateMachine: cancelado en RESPONDING")
            raise
        except Exception as exc:
            _log_error(state, exc, bus)
        finally:
            await _cleanup(gateway, playback)
