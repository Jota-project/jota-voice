"""Tests para _setup_logging — ver issue #28.

Verifica que ningún logger de librerías externas (específicamente
websockets, que loguea request lines con cabeceras HTTP) hereda el
nivel DEBUG del logger raíz. Sin esto, poner
`logging: {level: DEBUG}` en config.yaml expone client_key y
CF-Access-Client-Secret en ~/Library/Logs/jota-voice/stderr.log
(ya ocurrió en la práctica durante la sesión de debugging que
motivó la issue #28).
"""
from __future__ import annotations

import logging

import pytest

from app.voice_client import _setup_logging


@pytest.fixture(autouse=True)
def _restore_logging():
    """basicConfig altera estado global; guardar/restaurar niveles y
    handlers del raíz (si el raíz no tiene handlers, p. ej. con
    ``-p no:logging``, basicConfig le añade un StreamHandler que se
    quedaba puesto para el resto de la sesión)."""
    root = logging.getLogger()
    saved_root = root.level
    saved_handlers = list(root.handlers)
    saved_websockets = logging.getLogger("websockets").level
    yield
    for h in list(root.handlers):
        if h not in saved_handlers:
            root.removeHandler(h)
    root.setLevel(saved_root)
    logging.getLogger("websockets").setLevel(saved_websockets)


def test_websockets_logger_is_info_after_setup_logging_debug() -> None:
    """El bug que cierra este test: basicConfig(level=DEBUG) deja
    getLogger('websockets') en DEBUG porque los loggers hijos heredan
    del raíz si no tienen nivel propio."""
    _setup_logging("DEBUG")
    assert logging.getLogger("websockets").level == logging.INFO


def test_websockets_logger_is_info_after_setup_logging_warning() -> None:
    """Idempotencia: incluso si el nivel raíz pedido es WARNING, websockets
    no debe bajar de INFO (un fix que solo se aplicara en la rama DEBUG
    dejaría el bug vivo si alguien pone DEBUG programáticamente)."""
    _setup_logging("WARNING")
    assert logging.getLogger("websockets").level == logging.INFO

def test_abrir_logs_apunta_al_fichero_donde_va_el_logging() -> None:
    """"Abrir logs" abre DEFAULT_LOG_PATH: debe ser el StandardErrorPath del
    LaunchAgent, porque basicConfig escribe en stderr (antes abría
    stdout.log, que queda casi vacío)."""
    from pathlib import Path

    from app.voice_client import DEFAULT_LOG_PATH

    assert DEFAULT_LOG_PATH.endswith("/stderr.log")
    plist_gen = Path(__file__).resolve().parents[3] / "install/macos/launchd-plist.sh"
    text = plist_gen.read_text()
    assert "<key>StandardErrorPath</key><string>$4/stderr.log</string>" in text
    assert DEFAULT_LOG_PATH == "~/Library/Logs/jota-voice/stderr.log"
