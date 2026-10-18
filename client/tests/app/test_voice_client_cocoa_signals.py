"""Integración real del modo Cocoa de voice_client: señales y apagado.

Cada escenario corre ``_run_with_cocoa_menubar`` en un subproceso, con
NSApp real en su hilo principal y un ``main()`` falso que imita lo que
importa del real: espera a ``external_stop_event``, hace su limpieza y en
el ``finally`` llama a ``menubar_backend.stop()``.

Antes, en modo Cocoa, SIGTERM mataba el proceso en seco (exit 143): sin un
handler Python instalado CPython no escribe en el wakeup fd, así que el
observer NSFileHandle nunca se enteraba y no había apagado ordenado
(ni audio.stop() ni cierre del gateway).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Cocoa solo en macOS")

try:
    import AppKit  # noqa: F401
except ImportError:  # pragma: no cover
    pytestmark = pytest.mark.skip(reason="pyobjc no instalado")

CLIENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TIMEOUT_S = 20.0

_SCRIPT = """
import asyncio, sys, time
sys.path.insert(0, {client!r})
from PyObjCTools import AppHelper
from app import voice_client
from config import Config, GatewayConfig, MenubarConfig
from ui.menubar_cocoa import CocoaMenubarBackend

MODE = {mode!r}

def _ready():
    print("READY", flush=True)

async def _fake_main(cfg, menubar_backend, *, external_stop_event=None):
    try:
        if MODE == "quit_while_starting":
            # Imita "Arrancando…": Salir antes de set_commands().
            AppHelper.callAfter(menubar_backend.quitApp_, None)
        else:
            # Solo cuando el run loop de Cocoa ya corre.
            AppHelper.callAfter(_ready)
        await external_stop_event.wait()
        print("ORDERLY_SHUTDOWN", flush=True)
        if MODE == "cleanup_raises":
            raise RuntimeError("fallo en la limpieza antes de stop()")
    finally:
        if MODE != "cleanup_raises":
            menubar_backend.stop()

voice_client.main = _fake_main
cfg = Config(gateway=GatewayConfig(client_key="x", host="localhost"))
backend = CocoaMenubarBackend(MenubarConfig())
if MODE == "cleanup_raises":
    # La señal llega igual; la limpieza revienta antes de stop().
    AppHelper.callLater(0.5, _ready)
t0 = time.time()
voice_client._run_with_cocoa_menubar(cfg, backend)
print("RUNLOOP_RETURNED %.2f" % (time.time() - t0), flush=True)
"""


def _spawn(mode: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", _SCRIPT.format(client=CLIENT_DIR, mode=mode)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=CLIENT_DIR,
    )


def _wait_ready(proc: subprocess.Popen) -> None:
    deadline = time.monotonic() + TIMEOUT_S
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if "READY" in line:
            return
        if not line and proc.poll() is not None:
            break
    proc.kill()
    out, err = proc.communicate()
    pytest.fail(f"el subproceso no llegó a READY\nstdout={out}\nstderr={err}")


def _finish(proc: subprocess.Popen) -> tuple[int, str, str]:
    try:
        out, err = proc.communicate(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        pytest.fail(f"el proceso no terminó (run loop colgado)\nstdout={out}\nstderr={err}")
    return proc.returncode, out, err


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_signal_triggers_orderly_shutdown_and_run_loop_returns(sig):
    proc = _spawn("signal")
    _wait_ready(proc)
    proc.send_signal(sig)
    code, out, err = _finish(proc)

    assert code == 0, f"exit {code} (¿muerto por la señal?)\nstdout={out}\nstderr={err}"
    assert "ORDERLY_SHUTDOWN" in out, out + err
    assert "RUNLOOP_RETURNED" in out, out + err


def test_menubar_stops_even_if_cleanup_raises_before_stop():
    """Si el finally de main() lanza antes de menubar_backend.stop(), el
    run loop debe pararse igualmente."""
    proc = _spawn("cleanup_raises")
    _wait_ready(proc)
    proc.send_signal(signal.SIGTERM)
    code, out, err = _finish(proc)

    assert code == 0, f"exit {code}\nstdout={out}\nstderr={err}"
    assert "RUNLOOP_RETURNED" in out, out + err


def test_quit_while_starting_also_stops_asyncio():
    """"Salir" durante "Arrancando…" (sin comandos) paraba el run loop pero
    dejaba vivo el hilo asyncio (no daemon): el proceso no terminaba."""
    proc = _spawn("quit_while_starting")
    code, out, err = _finish(proc)

    assert code == 0, f"exit {code}\nstdout={out}\nstderr={err}"
    assert "ORDERLY_SHUTDOWN" in out, out + err
    assert "RUNLOOP_RETURNED" in out, out + err
