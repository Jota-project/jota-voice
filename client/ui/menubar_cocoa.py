"""CocoaMenubarBackend — NSStatusItem nativo de macOS con pyobjc.

IMPORTANTE: este módulo importa AppKit al cargarse. El registry solo lo
intenta en darwin Y captura ImportError si pyobjc no está instalado.
"""
from __future__ import annotations

import logging
from typing import Optional

import AppKit
import Foundation

from config import MenubarConfig

from .menubar_base import MenubarCommands, _SharedState

log = logging.getLogger(__name__)


# Mapeo de estado a SF Symbol. SF Symbols es el set de iconos vectoriales
# incluido en macOS; "template=True" deja que el sistema pinte según tema.
_STATE_ICONS = {
    "idle":      "mic",
    "listening": "ear",
    "thinking":  "brain",
    "speaking":  "speaker.wave.2",
    "error":     "exclamationmark.triangle",
    "cancelled": "xmark.circle",
}


class CocoaMenubarBackend:
    def __init__(self, cfg: MenubarConfig, shared: Optional[_SharedState] = None) -> None:
        self._cfg = cfg
        self._shared = shared or _SharedState()
        self._commands: Optional[MenubarCommands] = None
        self._app: Optional[AppKit.NSApplication] = None
        self.status_item: Optional[AppKit.NSStatusItem] = None
        self._menu: Optional[AppKit.NSMenu] = None
        self._header_item: Optional[AppKit.NSMenuItem] = None
        self._pause_menu_item: Optional[AppKit.NSMenuItem] = None
        self._timer: Optional[Foundation.NSTimer] = None

        self._build_status_item()

    # ------------------------------------------------------------------
    # Construcción
    # ------------------------------------------------------------------

    def _build_status_item(self) -> None:
        self.status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength
        )
        if self.status_item.button() is not None:
            self.status_item.button().setImage_(self._icon_for_state(self._shared.state))
            self.status_item.button().setImagePosition_(AppKit.NSImageOnly)
            self.status_item.button().setToolTip_("jota-voice")

        self._menu = AppKit.NSMenu.alloc().init()
        self.status_item.setMenu_(self._menu)

        # 1. Cabecera no seleccionable
        self._header_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            self._header_title(), None, ""
        )
        self._header_item.setEnabled_(False)
        self._menu.addItem_(self._header_item)

        # 2. Submenú "Servicio"
        servicio_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Servicio", None, ""
        )
        servicio_menu = AppKit.NSMenu.alloc().init()
        self._pause_menu_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Pausar escucha", "togglePause:", ""
        )
        self._pause_menu_item.setTarget_(self)
        servicio_menu.addItem_(self._pause_menu_item)
        shutdown_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Apagar servicio", "shutdownService:", ""
        )
        shutdown_item.setTarget_(self)
        servicio_menu.addItem_(shutdown_item)
        servicio_item.setSubmenu_(servicio_menu)
        self._menu.addItem_(servicio_item)

        self._menu.addItem_(AppKit.NSMenuItem.separatorItem())

        # 4. Abrir logs
        logs_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Abrir logs", "openLogs:", ""
        )
        logs_item.setTarget_(self)
        self._menu.addItem_(logs_item)

        # 5. Abrir configuración
        cfg_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Abrir configuración", "openConfig:", ""
        )
        cfg_item.setTarget_(self)
        self._menu.addItem_(cfg_item)

        self._menu.addItem_(AppKit.NSMenuItem.separatorItem())

        # 7. Acerca de
        about_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Acerca de jota-voice", "showAbout:", ""
        )
        about_item.setTarget_(self)
        self._menu.addItem_(about_item)

        # 8. Salir
        quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Salir", "quitApp:", ""
        )
        quit_item.setTarget_(self)
        self._menu.addItem_(quit_item)

    def _icon_for_state(self, state: str) -> AppKit.NSImage:
        symbol_name = _STATE_ICONS.get(state, "mic")
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            symbol_name, None
        )
        if image is not None:
            image.setTemplate_(True)
            return image
        # Fallback a un punto si el SF Symbol no existe en esta versión de macOS
        return AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "circle", None
        )

    def _header_title(self) -> str:
        state, _, errors, paused = self._shared.read()
        suffix = " (pausado)" if paused else ""
        if errors:
            return f"{state.upper()} — {errors} error(es){suffix}"
        return f"{state.upper()}{suffix}"

    # ------------------------------------------------------------------
    # API pública (llamada desde asyncio a través de _SharedState o
    # directamente desde el hilo Cocoa vía performSelectorOnMainThread)
    # ------------------------------------------------------------------

    def set_state(self, state: str) -> None:
        self._shared.update(state=state)
        self._request_repaint()

    def set_status_text(self, text: str) -> None:
        self._shared.update(last_text=text)
        self._request_repaint()

    def set_listening_paused(self, paused: bool) -> None:
        self._shared.update(listening_paused=paused)
        self._request_repaint()

    def set_errors_count(self, n: int) -> None:
        self._shared.update(errors_count=n)
        self._request_repaint()

    def set_commands(self, cmds: MenubarCommands) -> None:
        self._commands = cmds

    def _request_repaint(self) -> None:
        """Fuerza un repintado inmediato en vez de esperar al siguiente
        tick del NSTimer (hasta 1/refresh_hz segundos de retraso — con el
        default de 5Hz, hasta 200ms). El icono debe reaccionar en el
        instante en que cambia el estado (p.ej. al detectar la wake word),
        no en el siguiente tick periódico. No-op si run_forever() todavía
        no arrancó (self._app sigue en None)."""
        if self._app is not None:
            self._app.performSelectorOnMainThread_withObject_waitUntilDone_(
                "tick:", None, False
            )

    # ------------------------------------------------------------------
    # Runloop Cocoa
    # ------------------------------------------------------------------

    def run_forever(self) -> None:
        """Arranca NSApp.run() y bloquea el hilo que lo invoca.

        DEBE llamarse desde el hilo principal real del proceso. AppKit
        asume que NSApplication vive siempre en ese hilo para conectar
        con WindowServer; construir el NSStatusItem en un hilo y correr
        NSApp.run() en otro distinto cuelga el proceso (comprobado
        empíricamente). El caller (voice_client.py) es responsable de
        mover asyncio a un hilo de trabajo y dejar este hilo libre para
        el run loop de Cocoa.
        """
        self._app = AppKit.NSApplication.sharedApplication()
        self._app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

        interval = 1.0 / max(1.0, min(30.0, self._cfg.refresh_hz))
        self._timer = Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            interval, self, "tick:", None, True
        )
        Foundation.NSRunLoop.currentRunLoop().addTimer_forMode_(
            self._timer, Foundation.NSRunLoopCommonModes
        )
        self._app.run()

    def stop(self) -> None:
        """Detiene NSApp. Seguro de llamar desde cualquier hilo: salta al
        hilo principal (donde corre run_forever) vía
        performSelectorOnMainThread_withObject_waitUntilDone_. ``stop:`` hace
        retornar el run loop; ``terminate:`` llamaría a exit() y podría cortar
        el apagado asíncrono a mitad."""
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self._app is not None:
            self._app.performSelectorOnMainThread_withObject_waitUntilDone_(
                "stop:", None, False
            )
            # stop: solo se observa al terminar de procesar un NSEvent; el
            # selector del run loop no genera uno por sí mismo.
            make_event = getattr(
                AppKit.NSEvent,
                "otherEventWithType_location_modifierFlags_timestamp_"
                "windowNumber_context_subtype_data1_data2_",
            )
            wake_event = make_event(
                AppKit.NSEventTypeApplicationDefined,
                (0.0, 0.0),
                0,
                0.0,
                0,
                None,
                0,
                0,
                0,
            )
            self._app.postEvent_atStart_(wake_event, True)

    # ------------------------------------------------------------------
    # Tick del NSTimer (corre en hilo Cocoa)
    # ------------------------------------------------------------------

    def tick_(self, _timer) -> None:  # noqa: D401 — selector name
        state, last_text, _errors, paused = self._shared.read()

        if self.status_item and self.status_item.button():
            self.status_item.button().setImage_(self._icon_for_state(state))
            self.status_item.button().setTitle_(self._title_for_state(state))

        if self._header_item is not None:
            self._header_item.setTitle_(self._header_title())

        if self._pause_menu_item is not None:
            self._pause_menu_item.setTitle_(
                "Reanudar escucha" if paused else "Pausar escucha"
            )

    def _title_for_state(self, state: str) -> str:
        # El estado se ve también en el icono de la cabecera; dejamos el
        # botón solo con el icono salvo que el sistema no soporte SF Symbol.
        return ""

    # ------------------------------------------------------------------
    # Acciones de menú (corren en hilo Cocoa)
    # ------------------------------------------------------------------

    def togglePause_(self, _sender) -> None:
        if self._commands is not None:
            self._commands.on_toggle_pause()

    def shutdownService_(self, _sender) -> None:
        if self._commands is not None:
            self._commands.on_shutdown_service()

    def openLogs_(self, _sender) -> None:
        if self._commands is not None:
            self._commands.on_open_logs()

    def openConfig_(self, _sender) -> None:
        if self._commands is not None:
            self._commands.on_open_config()

    def showAbout_(self, _sender) -> None:
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("jota-voice")
        alert.setInformativeText_("Cliente de voz universal del ecosistema Jota.")
        alert.runModal()

    def quitApp_(self, _sender) -> None:
        if self._commands is not None:
            self._commands.on_quit()
        else:
            # set_commands() nunca se wireó (p.ej. audio.start() falló antes
            # de llegar a esa línea de main()) — no hay a quién delegar el
            # apagado limpio del asyncio loop, así que cerramos la app
            # directamente en vez de dejar "Salir" sin efecto.
            self.stop()
