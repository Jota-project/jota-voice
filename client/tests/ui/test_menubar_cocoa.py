"""Tests Cocoa-only: skipped salvo en macOS con pyobjc disponible."""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="Cocoa tests only run on macOS"
)

pyobjc_available = False
try:
    import AppKit  # noqa: F401
    pyobjc_available = True
except ImportError:
    pass

if not pyobjc_available:
    pytestmark = pytest.mark.skip(reason="pyobjc not installed")


@pytest.fixture
def backend():
    """No llama a start(): NSApp.run() debe correr en el hilo principal del
    proceso (así lo indica la arquitectura del plan), pero pytest ya ejecuta
    los tests en ese hilo principal — construir el NSStatusItem ahí y luego
    lanzar NSApp.run() en un hilo secundario dentro de start() cuelga
    (comprobado empíricamente: la construcción ocurre en el hilo principal,
    pero start() mueve NSApplication a un hilo distinto, lo que rompe la
    conexión con WindowServer). Por eso los tests ejercitan la creación
    del status item y las mutaciones de estado sin arrancar el run loop real;
    la verificación de start()/stop() en vivo queda para el checklist manual
    del Task 11 (fuera del alcance de un agente sin sesión GUI interactiva)."""
    from config import MenubarConfig
    from ui.menubar_base import _SharedState
    from ui.menubar_cocoa import CocoaMenubarBackend

    cfg = MenubarConfig(enabled=True, refresh_hz=20.0)
    shared = _SharedState()
    b = CocoaMenubarBackend(cfg, shared)
    yield b
    b.stop()


def test_status_item_created(backend):
    assert backend.status_item is not None
    assert backend.status_item.button() is not None


def test_set_state_updates_shared(backend):
    backend.set_state("listening")
    assert backend._shared.read()[0] == "listening"


def test_set_listening_paused_updates_label_after_tick(backend):
    """Tras un tick del timer, la etiqueta del item de pausa refleja el estado."""
    backend.set_listening_paused(True)
    backend.tick_(None)  # simula el tick del NSTimer sin arrancar el run loop real
    pause_item = backend._pause_menu_item
    assert pause_item.title() == "Reanudar escucha"


def test_set_commands_does_not_raise(backend):
    from ui.menubar_base import MenubarCommands

    cmds = MenubarCommands(
        on_toggle_pause=lambda: None,
        on_open_logs=lambda: None,
        on_open_config=lambda: None,
        on_shutdown_service=lambda: None,
        on_quit=lambda: None,
    )
    backend.set_commands(cmds)


def test_quit_with_commands_calls_on_quit(backend):
    from ui.menubar_base import MenubarCommands

    called = []
    cmds = MenubarCommands(
        on_toggle_pause=lambda: None,
        on_open_logs=lambda: None,
        on_open_config=lambda: None,
        on_shutdown_service=lambda: None,
        on_quit=lambda: called.append(True),
    )
    backend.set_commands(cmds)
    backend.quitApp_(None)
    assert called == [True]


def test_quit_without_commands_terminates_app_directly(backend):
    """Si audio.start() falló antes de que set_commands() se wireara,
    'Salir' debe cerrar la app igualmente en vez de no hacer nada."""
    calls = []

    class FakeApp:
        def performSelectorOnMainThread_withObject_waitUntilDone_(self, sel, obj, wait):
            calls.append(sel)

    backend._app = FakeApp()
    backend.quitApp_(None)
    assert "terminate:" in calls
