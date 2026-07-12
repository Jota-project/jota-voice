from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
import os
import yaml


def _load_env_file(path: Path) -> None:
    """Carga variables KEY=VALUE de un .env sin pisar las ya definidas en el entorno."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass
class GatewayConfig:
    client_key: str
    host: str = ""
    port: int = 8004
    path: str = "/ws/stream"
    connect_timeout_s: float = 10.0
    url: Optional[str] = None

    @property
    def ws_url(self) -> str:
        if self.url:
            return self.url
        return f"ws://{self.host}:{self.port}{self.path}"


@dataclass
class DeviceConfig:
    id: str = "jota-voice"


@dataclass
class OWWConfig:
    host: str = "127.0.0.1"
    port: int = 10401
    wake_words: List[str] = field(default_factory=lambda: ["ok_nabu"])
    reconnect_backoff_s: List[float] = field(default_factory=lambda: [5.0, 10.0, 20.0, 60.0])
    idle_detection_timeout_s: float = 0.0  # 0.0 = sin timeout
    backend: str = "wyoming"


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    frames_per_buffer: int = 512
    preroll_seconds: float = 1.5
    silence_timeout_s: float = 1.5
    recording_timeout_s: float = 15.0
    vad_rms_threshold: float = 200.0
    input_device: Optional[int] = None
    output_device: Optional[int] = None
    backend: Optional[str] = None


@dataclass
class DisplayConfig:
    url: str = "http://127.0.0.1:8766"
    timeout_s: float = 2.0
    backend: Optional[str] = None


@dataclass
class MenubarConfig:
    enabled: bool = True
    refresh_hz: float = 5.0
    log_path: Optional[str] = None
    config_path: Optional[str] = None

    def __post_init__(self) -> None:
        if self.refresh_hz < 1.0:
            self.refresh_hz = 1.0
        elif self.refresh_hz > 30.0:
            self.refresh_hz = 30.0


@dataclass
class ControlConfig:
    port: int = 8765


@dataclass
class LoggingConfig:
    level: str = "INFO"


@dataclass
class Config:
    gateway: GatewayConfig
    device: DeviceConfig = field(default_factory=DeviceConfig)
    oww: OWWConfig = field(default_factory=OWWConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    menubar: MenubarConfig = field(default_factory=MenubarConfig)


def _gateway_from_dict(d: dict) -> GatewayConfig:
    if "client_key" not in d:
        raise ValueError("config.yaml: falta client_key en gateway")
    if "url" not in d and "host" not in d:
        raise ValueError("config.yaml: gateway necesita 'url' o 'host'")
    return GatewayConfig(
        client_key=d["client_key"],
        host=d.get("host", ""),
        port=int(d.get("port", 8004)),
        path=d.get("path", "/ws/stream"),
        connect_timeout_s=float(d.get("connect_timeout_s", 10.0)),
        url=d.get("url"),
    )


def _device_from_dict(d: dict) -> DeviceConfig:
    required = {"id"}
    missing = required - d.keys()
    if missing:
        raise ValueError(f"config.yaml: faltan campos en device: {missing}")
    return DeviceConfig(id=str(d["id"]))


def _oww_from_dict(d: dict) -> OWWConfig:
    return OWWConfig(
        backend=d.get("backend", "wyoming"),
        host=d.get("host", "127.0.0.1"),
        port=int(d.get("port", 10401)),
        wake_words=list(d.get("wake_words", ["ok_nabu"])),
        reconnect_backoff_s=[float(x) for x in d.get("reconnect_backoff_s", [5, 10, 20, 60])],
        idle_detection_timeout_s=float(d.get("idle_detection_timeout_s", 0.0)),
    )


def _audio_from_dict(d: dict) -> AudioConfig:
    return AudioConfig(
        backend=d.get("backend"),
        sample_rate=int(d.get("sample_rate", 16000)),
        channels=int(d.get("channels", 1)),
        frames_per_buffer=int(d.get("frames_per_buffer", 512)),
        preroll_seconds=float(d.get("preroll_seconds", 1.5)),
        silence_timeout_s=float(d.get("silence_timeout_s", 1.5)),
        recording_timeout_s=float(d.get("recording_timeout_s", 15.0)),
        vad_rms_threshold=float(d.get("vad_rms_threshold", 200.0)),
        input_device=d.get("input_device"),
        output_device=d.get("output_device"),
    )


def _display_from_dict(d: dict) -> DisplayConfig:
    return DisplayConfig(
        backend=d.get("backend"),
        url=d.get("url", ""),
        timeout_s=float(d.get("timeout_s", 2.0)),
    )


def _control_from_dict(d: dict) -> ControlConfig:
    return ControlConfig(
        port=int(d.get("port", 8765)),
    )


def _menubar_from_dict(d: dict) -> MenubarConfig:
    return MenubarConfig(
        enabled=bool(d.get("enabled", True)),
        refresh_hz=float(d.get("refresh_hz", 5.0)),
        log_path=d.get("log_path"),
        config_path=d.get("config_path"),
    )


def load_config(path: str | Path) -> Config:
    real_path = Path(path).resolve()
    _load_env_file(real_path.parent / ".env")
    with open(path) as f:
        data = yaml.safe_load(f)
    if "gateway" not in data:
        raise ValueError("config.yaml: sección 'gateway' obligatoria")
    if "device" not in data:
        raise ValueError("config.yaml: sección 'device' obligatoria")

    menubar_section = data.get("menubar", {})
    if os.environ.get("JOTA_DISABLE_MENUBAR") in ("1", "true", "yes"):
        menubar_section = {**menubar_section, "enabled": False}

    return Config(
        gateway=_gateway_from_dict(data["gateway"]),
        device=_device_from_dict(data["device"]),
        oww=_oww_from_dict(data.get("oww", {})),
        audio=_audio_from_dict(data.get("audio", {})),
        display=_display_from_dict(data.get("display", {})),
        control=_control_from_dict(data.get("control", {})),
        logging=LoggingConfig(level=data.get("logging", {}).get("level", "INFO")),
        menubar=_menubar_from_dict(menubar_section),
    )


if __name__ == "__main__":
    import sys
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg = load_config(cfg_path)
    print("Config cargada OK")
    print(f"  device:  {cfg.device.id}")
    print(f"  gateway: {cfg.gateway.ws_url}")
    print(f"  oww:     {cfg.oww.host}:{cfg.oww.port} (backend={cfg.oww.backend})")
    print(f"  audio:   backend={cfg.audio.backend}")
    print(f"  display: backend={cfg.display.backend} url={cfg.display.url!r}")
