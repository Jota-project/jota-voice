"""Test de regresión: las interfaces de backends deben resolver sus type hints."""
from __future__ import annotations

import typing

from backends.audio_base import AudioBackend
from backends.oww_base import OwWBackend


def test_get_queue_type_hints_resolve() -> None:
    hints = typing.get_type_hints(AudioBackend.get_queue)
    assert hints["return"] is not None


def test_run_forever_type_hints_resolve() -> None:
    hints = typing.get_type_hints(OwWBackend.run_forever)
    assert hints["audio"] is AudioBackend
