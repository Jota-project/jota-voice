"""Integración real del run loop de Cocoa: run_forever() con NSApp de
verdad en el hilo principal de un subproceso.

Los tests de test_menubar_cocoa.py no arrancan NSApp.run() (bloquearía el
hilo principal de pytest), así que no detectaron que stop() desde el hilo
asyncio dejaba de hacer retornar el run loop: el proceso se quedaba vivo e
invisible (sin icono ni timer). Cada escenario corre en un subproceso con
timeout; si el run loop no retorna, el subproceso no termina.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from tests.fakes.cocoa import requires_cocoa

pytestmark = requires_cocoa

CLIENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TIMEOUT_S = 15.0

_PRELUDE = """
import sys, threading, time
sys.path.insert(0, {client!r})
from PyObjCTools import AppHelper
from config import MenubarConfig
from ui.menubar_cocoa import CocoaMenubarBackend
b = CocoaMenubarBackend(MenubarConfig())
"""

_EPILOGUE = """
t0 = time.time()
b.run_forever()
assert b.status_item is None, "el NSStatusItem debe retirarse al salir"
print("RUNLOOP_RETURNED %.2f" % (time.time() - t0), flush=True)
"""

SCENARIOS = {
    # Apagado normal: voice_client llama a stop() desde el hilo asyncio.
    "stop_from_worker": """
def worker():
    time.sleep(0.3)
    b.stop()
threading.Thread(target=worker, daemon=True).start()
""",
    # stop() desde el propio hilo principal (p.ej. desde una acción de menú).
    "stop_from_main_thread": """
AppHelper.callLater(0.3, b.stop)
""",
    # "Salir" antes de set_commands() (error de arranque): quitApp_ → stop().
    "quit_without_commands": """
AppHelper.callLater(0.3, b.quitApp_, None)
""",
    # Varios stop() seguidos no deben romper nada.
    "double_stop_from_worker": """
def worker():
    time.sleep(0.3)
    b.stop()
    b.stop()
threading.Thread(target=worker, daemon=True).start()
""",
}


def _run_scenario(body: str) -> subprocess.CompletedProcess:
    script = _PRELUDE.format(client=CLIENT_DIR) + textwrap.dedent(body) + _EPILOGUE
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        cwd=CLIENT_DIR,
    )


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_run_forever_returns_after_stop(name):
    try:
        result = _run_scenario(SCENARIOS[name])
    except subprocess.TimeoutExpired as exc:  # el run loop no retornó
        pytest.fail(f"{name}: NSApp.run() no retornó tras stop() ({exc})")
    assert result.returncode == 0, result.stderr
    assert "RUNLOOP_RETURNED" in result.stdout, result.stdout + result.stderr
