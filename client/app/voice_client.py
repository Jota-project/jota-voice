#!/usr/bin/env python3
"""voice_client.py — Punto de entrada de jota-voice v2 (multiplataforma).

Instancia los backends vía registry, crea el EventBus, arranca tareas:
- OWW run_forever como task background permanente (vía WyomingBackend)
- Display run() como task background permanente
- Menubar UI (macOS) como task background permanente
- state_machine.run() como loop principal
- Gestión de señales (SIGTERM/SIGINT → shutdown limpio)

Uso: python client/app/voice_client.py config.yaml
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent  # .../client (voice_client.py ahora vive en client/app/)
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

from config import Config, load_config
from domain.event_bus import EventBus, VoiceEvent
from domain.state_machine import run as sm_run
from backends import registry
from backends.gateway_client import GatewayClient

from app.playback_engine import PlaybackEngine
from app.display_client import DisplayClient
from app import control_server

from ui.menubar_base import MenubarCommands
from ui.menubar_client import MenubarClient


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def main(
    cfg: Config,
    menubar_backend,
    *,
    external_stop_event: asyncio.Event | None = None,
) -> None:
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
    stop_event = external_stop_event if external_stop_event is not None else asyncio.Event()

    def _on_signal() -> None:
        log.info("Señal de parada recibida")
        stop_event.set()

    if external_stop_event is None:
        # Con menubar Cocoa activo, el hilo principal real del proceso
        # está ocupado corriendo NSApp.run() (ver _run_with_cocoa_menubar)
        # y las señales OS se gestionan ahí con signal.signal() clásico,
        # no con loop.add_signal_handler — esa API exige que el loop de
        # asyncio corra en el hilo principal del intérprete, y aquí corre
        # en un hilo de trabajo.
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _on_signal)

    # --- Arrancar captura de audio ---
    try:
        await audio.start()
    except Exception:
        log.exception("No se pudo arrancar la captura de audio; jota-voice no puede continuar")
        menubar_backend.set_state("error")
        menubar_backend.set_status_text(
            "Error: no se pudo iniciar el audio (revisa permisos de micrófono/dispositivo)"
        )
        return
    log.info("AudioCapture iniciado")

    cancel_event = asyncio.Event()

    # --- Task background permanente: Menubar UI (macOS) ---
    menubar_client = MenubarClient(menubar_backend)
    ui_queue: queue.Queue = queue.Queue()
    pause_event = asyncio.Event()

    def _open_logs() -> None:
        import platform
        path = cfg.menubar.log_path or os.path.expanduser(
            "~/Library/Logs/jota-voice/stdout.log"
        )
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", "-a", "Console", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            log.warning("Menubar: no se pudo abrir logs (%s): %s", path, exc)

    def _open_config() -> None:
        import platform
        path = cfg.menubar.config_path or os.path.expanduser(
            "~/Library/Application Support/jota-voice/config.yaml"
        )
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            log.warning("Menubar: no se pudo abrir config (%s): %s", path, exc)

    def _shutdown_service() -> None:
        uid = os.getuid()
        try:
            subprocess.run(
                ["launchctl", "disable", f"gui/{uid}/com.jota.voice"],
                check=False, timeout=5,
            )
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}/com.jota.voice"],
                check=False, timeout=5,
            )
        except Exception as exc:
            log.warning("Menubar: launchctl falló: %s", exc)
        # Estas closures se invocan desde el hilo que ejecute la acción de
        # menú (con CocoaMenubarBackend, el hilo principal) — nunca desde
        # el hilo de asyncio. asyncio.Event.set() no es thread-safe, así
        # que hay que cruzar al loop vía call_soon_threadsafe.
        loop.call_soon_threadsafe(stop_event.set)

    def _quit() -> None:
        loop.call_soon_threadsafe(stop_event.set)

    cmds = MenubarCommands(
        on_toggle_pause=lambda: ui_queue.put_nowait("toggle_pause"),
        on_open_logs=_open_logs,
        on_open_config=_open_config,
        on_shutdown_service=_shutdown_service,
        on_quit=_quit,
    )
    menubar_backend.set_commands(cmds)

    menubar_task = asyncio.create_task(
        menubar_client.run(bus, ui_queue, pause_event), name="menubar"
    )

    # --- Task background permanente: OWW (pausable) ---
    async def _oww_loop() -> None:
        while True:
            if pause_event.is_set():
                await asyncio.sleep(0.2)
                continue
            t = asyncio.create_task(oww.run_forever(audio, _oww_on_wake))
            pause_wait = asyncio.create_task(pause_event.wait())
            done, pending = await asyncio.wait(
                [t, pause_wait],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for p in pending:
                p.cancel()
                try:
                    await p
                except asyncio.CancelledError:
                    pass
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    oww_task = asyncio.create_task(_oww_loop(), name="oww_listener")

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

        menubar_task.cancel()
        try:
            await menubar_task
        except asyncio.CancelledError:
            pass

        if hasattr(menubar_backend, "stop"):
            menubar_backend.stop()

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


def _run_with_cocoa_menubar(cfg: Config, menubar_backend) -> None:
    """Corre asyncio en un hilo de trabajo y deja el hilo principal libre
    para NSApp.run().

    AppKit asume que NSApplication vive siempre en el hilo principal real
    del proceso para conectar con WindowServer; construir el NSStatusItem
    en un hilo y correr NSApp.run() en otro distinto cuelga el proceso
    (comprobado empíricamente durante el desarrollo de este módulo).
    """
    log = logging.getLogger(__name__)
    loop_holder: dict = {}
    stop_holder: dict = {}
    loop_ready = threading.Event()

    def _asyncio_thread() -> None:
        async def _runner() -> None:
            stop_event = asyncio.Event()
            stop_holder["stop_event"] = stop_event
            loop_holder["loop"] = asyncio.get_running_loop()
            loop_ready.set()
            await main(cfg, menubar_backend, external_stop_event=stop_event)

        try:
            asyncio.run(_runner())
        except Exception:
            log.exception("El hilo de asyncio terminó con un error no controlado")
            # Por si el fallo ocurrió antes de que _runner() llegara a
            # loop_ready.set(): el hilo principal no debe quedarse
            # esperando los 5s completos del timeout.
            loop_ready.set()
            try:
                menubar_backend.set_state("error")
                menubar_backend.set_status_text(
                    "Error interno: revisa los logs de jota-voice"
                )
            except Exception:
                log.exception("No se pudo reflejar el error en el menubar")

    t = threading.Thread(target=_asyncio_thread, name="jota-asyncio", daemon=False)
    t.start()
    if not loop_ready.wait(timeout=5.0):
        log.error("El hilo de asyncio no arrancó a tiempo")

    def _handle_os_signal(signum, _frame) -> None:
        log.info("Señal de parada recibida (%s)", signum)
        loop = loop_holder.get("loop")
        stop_event = stop_holder.get("stop_event")
        if loop is None or stop_event is None:
            return
        try:
            loop.call_soon_threadsafe(stop_event.set)
        except RuntimeError:
            log.debug("El loop de asyncio ya estaba cerrado; señal ignorada")

    signal.signal(signal.SIGINT, _handle_os_signal)
    signal.signal(signal.SIGTERM, _handle_os_signal)

    menubar_backend.run_forever()  # bloquea el hilo principal (NSApp.run)
    t.join(timeout=10.0)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Uso: {sys.argv[0]} <config.yaml>", file=sys.stderr)
        sys.exit(1)

    _cfg = load_config(sys.argv[1])
    _setup_logging(_cfg.logging.level)
    _menubar_backend = registry.make_menubar(_cfg)

    if hasattr(_menubar_backend, "run_forever"):
        _run_with_cocoa_menubar(_cfg, _menubar_backend)
    else:
        asyncio.run(main(_cfg, _menubar_backend))
