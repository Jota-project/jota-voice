"""Marca común para los tests que necesitan Cocoa real (issue #75 / #112).

Antes cada fichero repetía el ``try: import AppKit`` y su propio
``pytestmark``; ``test_voice_client.py`` además usaba un
``pytestmark_cocoa`` que solo miraba la plataforma (no pyobjc) y que pytest
no aplica solo: un test nuevo que olvidara el decorador corría en Linux y
fallaba al importar AppKit.

Uso, a nivel de módulo o como decorador::

    from tests.fakes.cocoa import requires_cocoa
    pytestmark = requires_cocoa
"""
from __future__ import annotations

import sys

import pytest


def _pyobjc_available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import AppKit  # noqa: F401
    except ImportError:
        return False
    return True


PYOBJC_AVAILABLE = _pyobjc_available()

requires_cocoa = pytest.mark.skipif(
    not PYOBJC_AVAILABLE,
    reason="requiere macOS con pyobjc (AppKit)",
)
