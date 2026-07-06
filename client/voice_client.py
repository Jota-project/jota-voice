#!/usr/bin/env python3
"""voice_client.py — Punto de entrada de jota-voice v2 (multiplataforma).

Instancia los backends vía registry, crea el EventBus, arranca tareas:
- OWW run_forever como task background permanente (vía WyomingBackend)
- Display run() como task background permanente
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


def _apply_termux_hosts() -> None:
    """Parchea socket.getaddrinfo para leer el /etc/hosts de Termux.

    Android usa bionic como libc, que delega la resolución al DNS daemon del
    sistema (netd). Ese daemon solo lee /system/etc/hosts (requiere root).
    Este patch intercepta getaddrinfo/gethostbyname ANTES de que lleguen a
    bionic para cubrir primero la tabla local de Termux.
    Es un no-op si el fichero no existe (funciona en Mac/Linux normales).
    """
    import socket

    from backends.platform_detect import TERMUX_HOSTS_PATH

    _table: dict[str, str] = {}
    try:
        with open(TERMUX_HOSTS_PATH) as fh:
            for line in fh:
                line = line.split("#")[0].strip()
                parts = line.split()
                if len(parts) >= 2:
                    ip = parts[0]
                    for name in parts[1:]:
                        _table[name.lower()] = ip
    except FileNotFoundError:
        return

    if not _table:
        return

    _orig_getaddrinfo = socket.getaddrinfo

    def _getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
        if isinstance(host, str):
            host = _table.get(host.lower(), host)
        return _orig_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = _getaddrinfo

    _orig_gethostbyname = socket.gethostbyname

    def _gethostbyname(hostname):
        if isinstance(hostname, str):
            hostname = _table.get(hostname.lower(), hostname)
        return _orig_gethostbyname(hostname)

    socket.gethostbyname = _gethostbyname

    logging.getLogger(__name__).debug(
        "Termux hosts aplicados: %d entradas", len(_table)
    )


_apply_termux_hosts()

from config import load_config
from event_bus import EventBus, VoiceEvent
from backends import registry
from backends.gateway_client import GatewayClient
from playback_engine import PlaybackEngine
from display_client import DisplayClient
from state_machine import run as sm_run
import control_server


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
    log.info("jota-voice v2 arrancando… device=%s", cfg.device.id)

    # --- Crear bus + backends via registry ---
    bus = EventBus()

    async def _oww_on_wake(name: str) -> None:
        bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": name}))

    audio = registry.make_audio(cfg)
    oww = registry.make_oww(cfg, on_wake_word=_oww_on_wake)
    display_backend = registry.make_display(cfg)
    gateway = GatewayClient(cfg.gateway, device_id=cfg.device.id)
    playback = PlaybackEngine(bus, audio)
    display = DisplayClient(display_backend)

    log.info("Backends: audio=%s oww=%s display=%s",
             audio.__class__.__name__, oww.__class__.__name__, display_backend.__class__.__name__)

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

    cancel_event = asyncio.Event()

    # --- Task background permanente: OWW (detección persistente de wake word) ---
    oww_task = asyncio.create_task(
        oww.run_forever(audio, _oww_on_wake), name="oww_listener"
    )

    # --- Task background permanente: DisplayClient ---
    display_task = asyncio.create_task(display.run(bus), name="display")

    # --- Task background: ControlServer ---
    control_task = asyncio.create_task(
        control_server.run(cfg.control, cancel_event), name="control_server"
    )

    # --- Task principal: StateMachine ---
    sm_task = asyncio.create_task(
        sm_run(cfg, bus, audio, gateway, playback, cancel_event), name="state_machine"
    )

    stop_task = asyncio.create_task(stop_event.wait(), name="stop_signal")

    try:
        done, pending = await asyncio.wait(
            [sm_task, stop_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    finally:
        log.info("Apagando jota-voice…")

        sm_task.cancel()
        oww_task.cancel()
        display_task.cancel()
        control_task.cancel()
        stop_task.cancel()

        await asyncio.gather(
            sm_task, oww_task, display_task, control_task, stop_task,
            return_exceptions=True,
        )

        await audio.stop()

        try:
            await gateway.disconnect()
        except Exception:
            pass

        try:
            await oww.disconnect()
        except Exception:
            pass

        bus.close()
        log.info("jota-voice apagado limpiamente")


async def _on_wake(bus: EventBus, name: str) -> None:
    """Callback por defecto de wake word — publica en el bus."""
    bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": name}))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Uso: {sys.argv[0]} <config.yaml>", file=sys.stderr)
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))