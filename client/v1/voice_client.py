import asyncio
import enum
import logging
import sys

import numpy as np

from audio import AudioIO
from config import Config, load_config
from display import DisplayClient
from gateway import GatewayClient
from oww import OWWClient

log = logging.getLogger(__name__)


class State(enum.Enum):
    IDLE = "idle"
    RECORDING = "recording"
    RESPONDING = "responding"  # PROCESSING + PLAYING fusionados


async def _oww_detection_loop(oww: OWWClient) -> str:
    return await oww.wait_for_detection()


async def idle(
    audio: AudioIO,
    oww: OWWClient,
    gw: GatewayClient,
    disp: DisplayClient,
    cfg: Config,
) -> State:
    await disp.set_state("idle")

    if not oww.is_connected:
        await oww.connect_with_backoff()

    queue = audio.get_capture_queue()
    # Drenar frames acumulados durante el estado anterior para evitar
    # false-positives en OWW con audio stale
    while not queue.empty():
        queue.get_nowait()
    detection_task = asyncio.create_task(_oww_detection_loop(oww))

    try:
        while not detection_task.done():
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=0.05)
            except asyncio.TimeoutError:
                continue

            f32 = np.frombuffer(frame, dtype=np.float32)
            int16_bytes = (f32 * 32768.0).clip(-32768, 32767).astype(np.int16).tobytes()

            try:
                await oww.send_audio_chunk(int16_bytes)
            except Exception as exc:
                log.warning("OWW error (%s), reconectando", exc)
                detection_task.cancel()
                try:
                    await detection_task
                except (asyncio.CancelledError, Exception):
                    pass
                await oww.disconnect()
                await oww.connect_with_backoff()
                detection_task = asyncio.create_task(_oww_detection_loop(oww))

        await detection_task
        return State.RECORDING

    except asyncio.CancelledError:
        detection_task.cancel()
        try:
            await detection_task
        except (asyncio.CancelledError, Exception):
            pass
        raise


async def recording(
    audio: AudioIO,
    oww: OWWClient,
    gw: GatewayClient,
    disp: DisplayClient,
    cfg: Config,
) -> State:
    await disp.set_state("listening")

    try:
        await asyncio.wait_for(gw.connect(), timeout=cfg.gateway.connect_timeout_s)
    except Exception as exc:
        log.error("Gateway no disponible: %s", exc)
        return State.IDLE

    preroll = audio.get_preroll()
    if preroll:
        await gw.send_audio_chunk(preroll)

    queue = audio.get_capture_queue()
    silence_frames_needed = int(
        cfg.audio.silence_timeout_s * cfg.audio.sample_rate / cfg.audio.frames_per_buffer
    )
    deadline = asyncio.get_event_loop().time() + cfg.audio.recording_timeout_s
    silence_count = 0
    voice_started = False

    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            log.info("Recording: timeout absoluto alcanzado")
            break
        try:
            frame = await asyncio.wait_for(queue.get(), timeout=min(remaining, 0.1))
        except asyncio.TimeoutError:
            continue

        await gw.send_audio_chunk(frame)

        if audio.is_silence(frame):
            if voice_started:
                silence_count += 1
                if silence_count >= silence_frames_needed:
                    log.info("Recording: fin por silencio")
                    break
        else:
            voice_started = True
            silence_count = 0

    await gw.send_end()
    return State.RESPONDING


async def responding(
    audio: AudioIO,
    oww: OWWClient,
    gw: GatewayClient,
    disp: DisplayClient,
    cfg: Config,
) -> State:
    await disp.set_state("thinking")
    processing_done = asyncio.Event()

    async def _receive_loop():
        async for event in gw.receive():
            if event.kind == "transcription_partial":
                log.debug("STT parcial: %s", event.text)
            elif event.kind == "transcription":
                log.info("STT final: %s", event.text)
            elif event.kind == "token":
                log.debug("token LLM: %r", event.content)
            elif event.kind == "service_status":
                log.debug("service_status: %s", event.raw)
            elif event.kind == "audio_chunk":
                if not processing_done.is_set():
                    processing_done.set()
                    await disp.set_state("response")
                await audio.play_chunk(event.audio)

    try:
        receive_task = asyncio.create_task(_receive_loop())
        processing_timeout = asyncio.create_task(asyncio.sleep(30.0))

        done, pending = await asyncio.wait(
            [receive_task, processing_timeout],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if processing_timeout in done and not processing_done.is_set():
            # Timeout antes del primer audio
            receive_task.cancel()
            try:
                await receive_task
            except (asyncio.CancelledError, Exception):
                pass
            log.warning("Responding: timeout 30s sin audio TTS")
        else:
            # receive_task terminó primero, o ya hay audio → esperar que acabe
            processing_timeout.cancel()
            if receive_task not in done:
                await receive_task

    except Exception as exc:
        log.error("Responding error: %s", exc)
    finally:
        await audio.drain_playback()
        await gw.disconnect()
        await disp.set_state("idle")

    return State.IDLE


STATE_HANDLERS = {
    State.IDLE: idle,
    State.RECORDING: recording,
    State.RESPONDING: responding,
}


async def run(cfg: Config) -> None:
    logging.basicConfig(
        level=getattr(logging, cfg.logging.level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    audio = AudioIO(cfg.audio)
    oww = OWWClient(cfg.oww)
    gw = GatewayClient(cfg.gateway)
    disp = DisplayClient(cfg.display)

    await audio.start()
    state = State.IDLE
    log.info("jota-voice-client arrancado")

    while True:
        try:
            next_state = await STATE_HANDLERS[state](audio, oww, gw, disp, cfg)
            log.debug("%s → %s", state.value, next_state.value)
            state = next_state
        except Exception as exc:
            log.exception("Error no gestionado en estado %s: %s", state.value, exc)
            try:
                await gw.disconnect()
            except Exception:
                pass
            state = State.IDLE


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Uso: {sys.argv[0]} <config.yaml>")
        sys.exit(1)
    cfg = load_config(sys.argv[1])
    asyncio.run(run(cfg))
