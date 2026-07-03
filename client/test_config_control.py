"""Tests de ControlConfig en config.py."""
from __future__ import annotations

import os
import sys
import tempfile

import yaml

_here = os.path.dirname(__file__)
if _here not in sys.path:
    sys.path.insert(0, _here)


def _write_cfg(data: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(data, f)
    f.close()
    return f.name


def test_control_port_default() -> None:
    path = _write_cfg({"gateway": {"host": "127.0.0.1", "client_key": "test"}, "device": {"id": "test-device"}})
    try:
        from config import load_config
        cfg = load_config(path)
        assert cfg.control.port == 8765, f"Esperaba 8765, got {cfg.control.port}"
    finally:
        os.unlink(path)


def test_control_port_custom() -> None:
    path = _write_cfg({
        "gateway": {"host": "127.0.0.1", "client_key": "test"},
        "device": {"id": "test-device"},
        "control": {"port": 9000},
    })
    try:
        from config import load_config
        cfg = load_config(path)
        assert cfg.control.port == 9000, f"Esperaba 9000, got {cfg.control.port}"
    finally:
        os.unlink(path)


if __name__ == "__main__":
    test_control_port_default()
    test_control_port_custom()
    print("=== TODOS LOS TESTS PASARON ===")
