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


def test_cocoa_backend_fulfils_both_protocols(backend):
    """Issue #69: si el backend Cocoa dejara de cumplir el contrato
    bloqueante (p.ej. renombrando run_forever), voice_client lo trataría
    como un backend normal y el icono no aparecería nunca, sin diagnóstico."""
    from ui.menubar_base import BlockingMenubarBackend, MenubarBackend, needs_main_thread

    assert isinstance(backend, MenubarBackend)
    assert isinstance(backend, BlockingMenubarBackend)
    assert needs_main_thread(backend)


def _noop_commands():
    from ui.menubar_base import MenubarCommands

    return MenubarCommands(
        on_toggle_pause=lambda: None,
        on_open_logs=lambda: None,
        on_open_config=lambda: None,
        on_shutdown_service=lambda: None,
        on_quit=lambda: None,
    )


def _items_by_title(menu):
    items = {}
    for i in range(menu.numberOfItems()):
        item = menu.itemAtIndex_(i)
        items[item.title()] = item
        if item.submenu() is not None:
            items.update(_items_by_title(item.submenu()))
    return items


def test_command_items_disabled_until_set_commands(backend):
    """Issue #71: el menú es visible desde el constructor, pero los clics se
    perdían en silencio hasta set_commands(). Hasta entonces los items que
    dependen de los comandos van deshabilitados y la cabecera dice
    "Arrancando…"; Salir y Acerca de siguen activos."""
    items = _items_by_title(backend._menu)
    gated = ["Pausar escucha", "Apagar servicio", "Abrir logs", "Abrir configuración"]

    assert not backend._menu.autoenablesItems()
    for title in gated:
        assert not items[title].isEnabled(), title
    assert items["Salir"].isEnabled()
    assert items["Acerca de jota-voice"].isEnabled()
    assert backend._header_item.title() == "Arrancando…"

    backend.tick_(None)
    for title in gated:
        assert not items[title].isEnabled(), title

    backend.set_commands(_noop_commands())
    backend.tick_(None)

    for title in gated:
        assert items[title].isEnabled(), title
    assert backend._header_item.title() != "Arrancando…"


def test_startup_error_is_not_hidden_behind_arrancando(backend):
    """Si audio.start() falla, set_commands() nunca llega pero el estado pasa
    a error: la cabecera debe enseñar el error, no "Arrancando…"."""
    backend.set_state("error")
    backend.tick_(None)

    assert backend._header_item.title() != "Arrancando…"


def test_set_commands_requests_repaint(backend):
    backend._app = _RecordingApp()
    scheduled = []
    backend._call_on_main = lambda fn, *args: scheduled.append((fn, args))

    backend.set_commands(_noop_commands())

    assert scheduled == [(backend.tick_, (None,))]


@pytest.mark.parametrize(
    "state, title",
    [
        ("idle", "En espera"),
        ("listening", "Escuchando"),
        ("thinking", "Pensando"),
        ("speaking", "Hablando"),
        ("error", "Error"),
        ("cancelled", "Cancelado"),
    ],
)
def test_header_title_is_spanish(backend, state, title):
    """Issue #97: la cabecera hacía state.upper() ("CANCELLED") mezclando
    inglés con el resto de etiquetas en español."""
    backend.set_commands(_noop_commands())
    backend.set_state(state)
    backend.tick_(None)

    assert backend._header_item.title() == title


def test_header_title_with_errors_and_pause(backend):
    backend.set_commands(_noop_commands())
    backend.set_state("cancelled")
    backend.set_errors_count(2)
    backend.set_listening_paused(True)
    backend.tick_(None)

    assert backend._header_item.title() == "Cancelado — 2 error(es) (pausado)"


def test_no_dead_title_for_state(backend):
    """Issue #98: _title_for_state() siempre devolvía "" (el botón es
    NSImageOnly)."""
    assert not hasattr(backend, "_title_for_state")


def test_show_about_uses_non_modal_standard_panel(backend, monkeypatch):
    """Issue #98: showAbout_ usaba NSAlert.runModal(), que bloquea el run
    loop principal (ni repintados ni el observer de señales de #104 mientras
    el diálogo está abierto). El panel estándar de Acerca de no es modal."""
    calls = []

    class _AboutApp(_RecordingApp):
        def activateIgnoringOtherApps_(self, flag):
            calls.append(("activate", flag))

        def orderFrontStandardAboutPanelWithOptions_(self, options):
            calls.append(("about", dict(options)))

    def _no_modal(*_args, **_kwargs):
        raise AssertionError("no debe usarse un NSAlert modal")

    import ui.menubar_cocoa as menubar_cocoa

    class _AppKitProxy:
        class NSAlert:
            alloc = staticmethod(_no_modal)

        def __getattr__(self, name):
            return getattr(AppKit, name)

    monkeypatch.setattr(menubar_cocoa, "AppKit", _AppKitProxy())
    backend._app = _AboutApp()

    backend.showAbout_(None)

    assert calls[0] == ("activate", True)
    assert calls[1][0] == "about"
    assert calls[1][1]["ApplicationName"] == "jota-voice"


def test_health_item_hidden_while_ok(backend):
    backend.set_commands(_noop_commands())
    backend.tick_(None)

    assert backend._health_item.isHidden()
    assert backend._menu.indexOfItem_(backend._health_item) == 1  # bajo la cabecera


def test_health_item_shows_discreet_info(backend):
    """#108: "Transcripción degradada" aparece como línea discreta bajo la
    cabecera, sin cambiar el icono de la barra."""
    from ui.menubar_cocoa import _icon_name

    backend.set_commands(_noop_commands())
    backend.set_health("info", "Transcripción degradada")
    backend.tick_(None)

    item = backend._health_item
    assert not item.isHidden()
    assert item.title() == "Transcripción degradada"
    assert item.isEnabled()  # clic → Abrir logs
    assert _icon_name("idle", "info") == "mic"
    assert "Transcripción degradada" in backend.status_item.button().toolTip()

    backend.set_health("ok", "")
    backend.tick_(None)
    assert item.isHidden()
    assert backend.status_item.button().toolTip() == "jota-voice"


def test_health_item_disabled_until_commands(backend):
    backend.set_health("warn", "Gateway: reconectando…")
    backend.tick_(None)

    assert not backend._health_item.isHidden()
    assert not backend._health_item.isEnabled()


def test_fatal_health_is_red(backend):
    backend.set_commands(_noop_commands())
    backend.set_health("fatal", "Gateway: client_key inválida")
    backend.tick_(None)

    title = backend._health_item.attributedTitle()
    color, _range = title.attribute_atIndex_effectiveRange_(
        AppKit.NSForegroundColorAttributeName, 0, None
    )
    assert title.string() == "Gateway: client_key inválida"
    assert color == AppKit.NSColor.systemRedColor()

    # Al volver a un aviso normal se quita el color.
    backend.set_health("info", "Transcripción degradada")
    backend.tick_(None)
    assert backend._health_item.title() == "Transcripción degradada"
    plain = backend._health_item.attributedTitle()
    assert plain is None or plain.attribute_atIndex_effectiveRange_(
        AppKit.NSForegroundColorAttributeName, 0, None
    )[0] != AppKit.NSColor.systemRedColor()


@pytest.mark.parametrize(
    "state, level, name",
    [
        ("idle", "ok", "mic"),
        ("idle", "info", "mic"),
        ("idle", "warn", "mic.slash"),
        ("idle", "fatal", "exclamationmark.octagon"),
        # Durante un turno manda el estado del turno.
        ("listening", "fatal", "ear"),
        ("speaking", "warn", "speaker.wave.2"),
        ("error", "warn", "exclamationmark.triangle"),
    ],
)
def test_icon_name_combines_state_and_health(state, level, name):
    from ui.menubar_cocoa import _icon_name

    assert _icon_name(state, level) == name


def test_health_icons_exist_as_sf_symbols():
    for name in ("mic.slash", "exclamationmark.octagon", "info.circle",
                 "exclamationmark.triangle", "xmark.octagon"):
        assert AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            name, None
        ) is not None, name


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


def test_quit_without_commands_stops_app_cleanly(backend):
    """Si audio.start() falló antes de que set_commands() se wireara,
    'Salir' debe cerrar la app igualmente en vez de no hacer nada."""
    selectors = []
    posted_events = []

    class FakeApp:
        def performSelectorOnMainThread_withObject_waitUntilDone_(self, sel, obj, wait):
            selectors.append(sel)

        def postEvent_atStart_(self, event, at_start):
            posted_events.append((event, at_start))

    backend._app = FakeApp()
    backend.quitApp_(None)

    assert selectors == ["stop:"]
    assert [(event.type(), at_start) for event, at_start in posted_events] == [
        (AppKit.NSEventTypeApplicationDefined, True)
    ]


class _StatusBarSpy:
    """Envuelve la NSStatusBar real y registra las retiradas (issue #68)."""

    def __init__(self, real):
        self._real = real
        self.removed = []

    def removeStatusItem_(self, item):
        self.removed.append(item)
        self._real.removeStatusItem_(item)


def test_stop_removes_status_item_from_system_bar(backend):
    """Issue #68: stop() debe retirar el NSStatusItem de la barra del
    sistema; si no, cada corrida de tests deja iconos huérfanos."""
    item = backend.status_item
    spy = _StatusBarSpy(backend._status_bar)
    backend._status_bar = spy

    backend.stop()

    assert spy.removed == [item]
    assert backend.status_item is None
    assert backend._menu is None


def test_stop_is_idempotent(backend):
    """Issue #68: el fixture vuelve a llamar a stop(); no debe fallar ni
    intentar retirar dos veces el mismo item."""
    spy = _StatusBarSpy(backend._status_bar)
    backend._status_bar = spy

    backend.stop()
    backend.stop()

    assert len(spy.removed) == 1


class _FakeTimer:
    def __init__(self):
        self.invalidated_on = []

    def invalidate(self):
        import threading

        self.invalidated_on.append(threading.current_thread())


class _RecordingApp:
    def __init__(self):
        self.selectors = []

    def performSelectorOnMainThread_withObject_waitUntilDone_(self, sel, obj, wait):
        self.selectors.append(sel)

    def postEvent_atStart_(self, event, at_start):
        pass


def test_stop_from_worker_thread_defers_teardown_to_main_thread(backend):
    """Issue #70: NSTimer.invalidate() debe ejecutarse en el hilo que
    instaló el timer (el principal). Desde el hilo asyncio, stop() solo
    programa el teardown en el hilo principal."""
    import threading

    timer = _FakeTimer()
    backend._timer = timer
    backend._app = _RecordingApp()
    scheduled = []
    backend._call_on_main = lambda fn, *args: scheduled.append((fn, args))

    worker = threading.Thread(target=backend.stop)
    worker.start()
    worker.join(timeout=5.0)

    assert timer.invalidated_on == [], "invalidate() no debe correr en el worker"
    assert [fn for fn, _ in scheduled] == [backend._teardown]
    assert backend._app.selectors == ["stop:"]

    # El hilo principal ejecuta lo programado.
    for fn, args in scheduled:
        fn(*args)
    assert timer.invalidated_on == [threading.main_thread()]
    assert backend.status_item is None


def test_request_repaint_runs_tick_on_main_thread_not_on_nsapp(backend):
    """_request_repaint enviaba ``tick:`` a NSApplication, que no responde a
    ese selector: cada set_* lanzaba un unrecognized selector en el hilo
    principal y el icono esperaba al siguiente tick del timer."""
    backend._app = _RecordingApp()
    scheduled = []
    backend._call_on_main = lambda fn, *args: scheduled.append((fn, args))

    backend.set_state("listening")

    assert backend._app.selectors == []
    assert scheduled == [(backend.tick_, (None,))]
    assert not AppKit.NSApplication.sharedApplication().respondsToSelector_("tick:")


def test_run_forever_tears_down_when_run_loop_returns(backend, monkeypatch):
    """Cuando NSApp.run() retorna (tras stop:), run_forever libera los
    recursos ya en el hilo principal."""

    class _ReturningApp:
        def setActivationPolicy_(self, policy):
            pass

        def run(self):
            pass

    import ui.menubar_cocoa as menubar_cocoa

    class _FakeNSApplication:
        @staticmethod
        def sharedApplication():
            return _ReturningApp()

    class _AppKitProxy:
        NSApplication = _FakeNSApplication

        def __getattr__(self, name):
            return getattr(AppKit, name)

    monkeypatch.setattr(menubar_cocoa, "AppKit", _AppKitProxy())
    backend.run_forever()

    assert backend.status_item is None
    assert backend._timer is None


def test_startup_error_text_is_shown_in_header(backend):
    """main() informa los errores de arranque (audio, handshake inicial) con
    set_state("error") + set_status_text(...), antes de set_commands(). Ese
    texto no se pintaba en ningún sitio."""
    backend.set_state("error")
    backend.set_status_text(
        "Error: no se pudo iniciar el audio (revisa permisos de micrófono/dispositivo)"
    )
    backend.tick_(None)

    assert backend._header_item.title().startswith("Error: no se pudo iniciar el audio")
