#!/usr/bin/env python3
"""voice_client.py — Punto de entrada de jota-voice v2.

Instancia todos los módulos, crea el EventBus, arranca tareas:
- DisplayClient.run() como task background permanente
- state_machine.run() como loop principal
- Gestión de señales (SIGTERM/SIGINT → shutdown limpio)

Uso: python client/voice_client.py config.yaml
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent  # .../client
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    import pyaudio
except ImportError:
    sys.exit("pyaudio no encontrado. Instálalo con: pip install pyaudio")

from config import load_config
from event_bus import EventBus
from audio_capture import AudioCapture
from oww_client import OWWClient
from gateway_client import GatewayClient
from playback_engine import PlaybackEngine
from display_client import DisplayClient
from state_machine import run as sm_run


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def main(config_path: str) -> None:
    cfg = load_config(config_path)
    _setup_logging(cfg.logging.level)
    log = logging.getLogger(__name__)
    log.info("jota-voice v2 arrancando…")

    # --- Crear módulos ---
    bus = EventBus()
    pa = pyaudio.PyAudio()
    audio = AudioCapture(cfg.audio)
    oww = OWWClient(cfg.oww)
    gateway = GatewayClient(cfg.gateway)
    playback = PlaybackEngine(bus, pa)
    display = DisplayClient(cfg.display, bus)

    # --- SIGTERM / SIGINT handler ---
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _on_signal() -> None:
        log.info("Señal de parada recibida")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _on_signal)

    # --- Arrancar captura de audio ---
    await audio.start()
    log.info("AudioCapture iniciado")

    # --- Task background permanente: DisplayClient ---
    display_task = asyncio.create_task(display.run(), name="display")

    # --- Task principal: StateMachine ---
    sm_task = asyncio.create_task(
        sm_run(cfg, bus, audio, oww, gateway, playback), name="state_machine"
    )

    # stop_event.wait() es una coroutine; hay que envolverla en task para
    # pasarla a asyncio.wait().
    stop_task = asyncio.create_task(stop_event.wait(), name="stop_signal")

    try:
        done, pending = await asyncio.wait(
            [sm_task, stop_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        # Cancelar la tarea que quedó pendiente (la que no terminó primero)
        for t in pending:
            t.cancel()
    finally:
        log.info("Apagando jota-voice…")

        # Cancelar state machine y display si siguen vivos
        sm_task.cancel()
        display_task.cancel()
        stop_task.cancel()

        await asyncio.gather(sm_task, display_task, stop_task, return_exceptions=True)

        # Teardown de recursos en orden inverso a su creación
        await audio.stop()

        try:
            await gateway.disconnect()
        except Exception:
            pass

        playback.close()
        pa.terminate()
        bus.close()

        log.info("jota-voice apagado limpiamente")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Uso: {sys.argv[0]} <config.yaml>", file=sys.stderr)
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
