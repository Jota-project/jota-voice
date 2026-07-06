"""Tests de config.py (sección control)."""
from __future__ import annotations

import os
import tempfile

import yaml


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


def test_ws_url_prioriza_url_sobre_host_port_path() -> None:
    from config import GatewayConfig
    cfg = GatewayConfig(client_key="x", url="wss://foo.example.com/api/gw/ws/stream")
    assert cfg.ws_url == "wss://foo.example.com/api/gw/ws/stream"


def test_ws_url_sin_url_construye_desde_host_port_path() -> None:
    from config import GatewayConfig
    cfg = GatewayConfig(client_key="x", host="myhost", port=1234, path="/p")
    assert cfg.ws_url == "ws://myhost:1234/p"


def test_load_config_acepta_url_sin_host() -> None:
    path = _write_cfg({
        "gateway": {"url": "wss://foo.example.com/ws", "client_key": "test"},
        "device": {"id": "test-device"},
    })
    try:
        from config import load_config
        cfg = load_config(path)
        assert cfg.gateway.ws_url == "wss://foo.example.com/ws"
    finally:
        os.unlink(path)


def test_load_config_rechaza_gateway_sin_url_ni_host() -> None:
    path = _write_cfg({
        "gateway": {"client_key": "test"},
        "device": {"id": "test-device"},
    })
    try:
        from config import load_config
        try:
            load_config(path)
            raise AssertionError("Debería haber lanzado ValueError")
        except ValueError:
            pass
    finally:
        os.unlink(path)
