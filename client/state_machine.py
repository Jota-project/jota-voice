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

import numpy as np

from client.audio_capture import AudioCapture
from client.config import Config
from client.event_bus import EventBus, VoiceEvent
from client.gateway_client import GatewayClient
from client.oww_client import OWWClient
from client.playback_engine import PlaybackEngine

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

async def _idle(
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

    # 3. Reconectar OWW si no está conectado
    if not oww.is_connected:
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

    task_a = asyncio.create_task(_send_audio_loop())
    try:
        wake_word = await oww.wait_for_detection()
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
    cfg: Config,
) -> None:
    """
    Estado RECORDING: captura la petición del usuario y la envía al gateway.

    1. Publica wake_word_detected + recording_started.
    2. Conecta al gateway (con timeout de config).
    3. Envía pre-roll.
    4. Envía audio nuevo hasta silencio o timeout.
    5. Envía end + publica recording_ended.
    """
    # 1. Publicar eventos
    bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": wake_word}))
    bus.publish(VoiceEvent(type="recording_started", data={}))

    # 2. Conectar gateway con timeout
    await asyncio.wait_for(
        gateway.connect(),
        timeout=cfg.gateway.connect_timeout_s,
    )
    log.debug("RECORDING: gateway conectado")

    # 3. Enviar pre-roll
    preroll = audio.get_preroll()
    if preroll:
        await gateway.send_audio(preroll)
        log.debug("RECORDING: pre-roll enviado (%d bytes)", len(preroll))

    # 4. Enviar audio nuevo hasta silencio o timeout
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
                break
        else:
            silence_count = 0
    else:
        log.info("RECORDING: timeout absoluto alcanzado (%.1fs)", cfg.audio.recording_timeout_s)

    # 5. Señal de fin
    await gateway.send_end()
    bus.publish(VoiceEvent(type="recording_ended", data={}))
    log.debug("RECORDING: end enviado")


async def _responding(
    bus: EventBus,
    gateway: GatewayClient,
    playback: PlaybackEngine,
) -> None:
    """
    Estado RESPONDING: recibe respuesta del gateway y la reproduce.

    - transcription / transcription_partial → publica VoiceEvent.
    - llm_token → PlaybackEngine.push_token() + publica VoiceEvent.
    - tts_chunk → PlaybackEngine.play_chunk() + publica VoiceEvent.
      (playback_started solo al primer chunk).
    - Timeout global 30s → error + abortar.
    - WS cierra → PlaybackEngine.drain() → playback_ended → IDLE.
    """
    playback_started = False

    async def _receive_loop() -> None:
        nonlocal playback_started
        async for gw_event in gateway.receive():
            if gw_event.type == "transcription":
                text = gw_event.data.get("text", "")
                bus.publish(VoiceEvent(type="transcription", data={"text": text}))
                log.info("RESPONDING: transcription → %r", text)

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

    try:
        await asyncio.wait_for(_receive_loop(), timeout=30.0)
    except asyncio.TimeoutError:
        log.warning("RESPONDING: timeout 30s")
        bus.publish(VoiceEvent(type="error", data={"message": "Timeout en estado RESPONDING"}))
        return

    await playback.drain()
    bus.publish(VoiceEvent(type="playback_ended", data={}))
    log.debug("RESPONDING: reproducción completada")


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
) -> None:
    """
    Loop principal: IDLE → RECORDING → RESPONDING → IDLE indefinidamente.

    Todos los módulos se pasan como argumentos (instanciados en voice_client.py).
    Cualquier excepción en cualquier estado publica error y vuelve a IDLE.
    """
    log.info("StateMachine: iniciando loop")

    while True:
        # ----------------------------------------------------------------
        # Estado IDLE
        # ----------------------------------------------------------------
        state = "IDLE"
        try:
            wake_word = await _idle(bus, audio, oww)
        except asyncio.CancelledError:
            log.info("StateMachine: cancelado en IDLE")
            raise
        except Exception as exc:
            log.error("Error en estado %s: %s", state, exc)
            bus.publish(VoiceEvent(type="error", data={"message": str(exc)}))
            try:
                await gateway.disconnect()
            except Exception:
                pass
            playback.reset()
            continue  # → volver a IDLE

        # ----------------------------------------------------------------
        # Estado RECORDING
        # ----------------------------------------------------------------
        state = "RECORDING"
        try:
            await _recording(wake_word, bus, audio, gateway, cfg)
        except asyncio.CancelledError:
            log.info("StateMachine: cancelado en RECORDING")
            try:
                await gateway.disconnect()
            except Exception:
                pass
            raise
        except Exception as exc:
            log.error("Error en estado %s: %s", state, exc)
            bus.publish(VoiceEvent(type="error", data={"message": str(exc)}))
            try:
                await gateway.disconnect()
            except Exception:
                pass
            playback.reset()
            continue  # → volver a IDLE

        # ----------------------------------------------------------------
        # Estado RESPONDING
        # ----------------------------------------------------------------
        state = "RESPONDING"
        try:
            await _responding(bus, gateway, playback)
        except asyncio.CancelledError:
            log.info("StateMachine: cancelado en RESPONDING")
            raise
        except Exception as exc:
            log.error("Error en estado %s: %s", state, exc)
            bus.publish(VoiceEvent(type="error", data={"message": str(exc)}))
        finally:
            # Desconectar gateway y resetear playback siempre al salir de RESPONDING
            try:
                await gateway.disconnect()
            except Exception:
                pass
            playback.reset()
        # → volver a IDLE (siguiente iteración del while)
