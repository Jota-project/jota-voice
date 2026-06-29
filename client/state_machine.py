"""
state_machine.py — Máquina de estados principal de jota-voice v2.

Coordina AudioCapture, OWWClient, GatewayClient y PlaybackEngine.
Toda la lógica de negocio vive aquí; no hace I/O directamente.

Estados: IDLE → RECORDING → RESPONDING → IDLE

Publica en EventBus en cada transición y en cada evento relevante.
Toda transición de error publica VoiceEvent(type="error") antes de volver a IDLE.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import numpy as np

from audio_capture import AudioCapture
from config import Config
from event_bus import EventBus, VoiceEvent
from gateway_client import GatewayClient
from oww_client import OWWClient
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
    audio: AudioCapture,
    oww: OWWClient,
) -> str:
    """
    Estado IDLE: escucha el micrófono y espera detección de wake word.

    1. Drena la queue de frames stale para evitar false-positives.
    2. Publica state_changed("idle").
    3. Reconecta OWW si es necesario.
    4. Task A: envía audio a OWW en loop.
    5. Task B (implícito): wait_for_detection() — cuando completa, cancela A.

    Returns el nombre del wake word detectado.
    Si cfg.oww.idle_detection_timeout_s > 0 y OWW no detecta en ese tiempo,
    lanza asyncio.TimeoutError (que el caller convierte en evento error).
    """
    q = audio.get_queue()

    # 1. Drenar audio stale ANTES de publicar para no contaminar otros módulos
    drained = 0
    while not q.empty():
        q.get_nowait()
        drained += 1
    if drained:
        log.debug("IDLE: descartados %d frames stale", drained)

    # 2. Publicar state_changed
    bus.publish(VoiceEvent(type="state_changed", data={"state": "idle"}))

    # 3. Desconectar OWW para resetear su estado interno (contadores de activación)
    #    y esperar a que el eco acústico del altavoz se extinga antes de escuchar.
    if oww.is_connected:
        await oww.disconnect()
        await asyncio.sleep(1.0)

    # 4. Vaciar frames acumulados durante el sleep antes de conectar
    while not q.empty():
        q.get_nowait()

    log.info("IDLE: reconectando OWW…")
    await oww.connect_with_backoff()

    # 4. Task A: enviar audio a OWW en loop
    async def _send_audio_loop() -> None:
        while True:
            frame = await q.get()  # float32 bytes
            pcm16 = (
                np.frombuffer(frame, np.float32).clip(-1.0, 1.0) * 32767.0
            ).astype(np.int16).tobytes()
            await oww.send_audio(pcm16)

    timeout = cfg.oww.idle_detection_timeout_s or None
    task_a = asyncio.create_task(_send_audio_loop())
    try:
        wake_word = await asyncio.wait_for(oww.wait_for_detection(), timeout=timeout)
        log.info("IDLE: wake word detectado → %r", wake_word)
        return wake_word
    finally:
        task_a.cancel()
        try:
            await task_a
        except asyncio.CancelledError:
            pass


async def _recording(
    wake_word: str,
    bus: EventBus,
    audio: AudioCapture,
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

    done, pending = await asyncio.wait(
        [receive_task, cancel_task],
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
    audio: AudioCapture,
    oww: OWWClient,
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
            wake_word = await _idle(cfg, bus, audio, oww)
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
