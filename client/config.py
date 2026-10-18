from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

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
    port: int | None = None      # si es None, no se añade puerto a la URL
    path: str = ""                  # si es "", no se añade path a la URL
    scheme: str = "ws"              # "ws" o "wss"; solo se usa cuando NO hay url completa
    connect_timeout_s: float = 10.0
    url: str | None = None
    # Issue #24: arquitectura de sesión. Default True → handshake eager
    # al startup del proceso. Si False, conserva el comportamiento reconnect-
    # per-turn (código actual). Sin override per-launch — solo YAML.
    persistent_session: bool = True
    # Issue #118: reconexión de la sesión persistente. Cada elemento es la
    # espera antes de ese reintento; el último se repite indefinidamente
    # (backoff con máximo). Se reinicia tras un `ready`.
    reconnect_backoff_s: list[float] = field(
        default_factory=lambda: [1.0, 2.0, 5.0, 10.0, 30.0]
    )
    # Issue #116/#118: keepalive de aplicación para que el gateway no cierre
    # la sesión por IDLE_TIMEOUT_S (300 s sin mensajes del cliente). Se
    # envía solo en IDLE y solo si no se ha enviado nada en este intervalo.
    # 0 lo desactiva (la sesión caerá a los 300 s y se reabrirá sola).
    keepalive_interval_s: float = 120.0
    # Keepalive de protocolo de la librería websockets (detecta peer muerto).
    # 0 desactiva el ping. No cuenta como actividad para el gateway.
    ping_interval_s: float = 20.0
    ping_timeout_s: float = 20.0

    @property
    def ws_url(self) -> str:
        # Si el usuario dio una URL completa, se respeta literal — sin
        # inyectar nada. Esto cubre el caso típico de gateway detrás de
        # un proxy inverso (Cloudflare Tunnel, nginx con TLS, etc.), donde
        # la URL ya viene con scheme, host, puerto y path correctos.
        if self.url:
            return self.url

        # Si NO hay url, construimos desde host (+ port opcional + path opcional).
        # NO inyectamos defaults: si el usuario no dio port, no se añade
        # (ej. "wss://host/ruta" se queda "wss://host/ruta", no "wss://host:443/ruta").
        if not self.host:
            raise ValueError(
                "GatewayConfig: falta 'url' o 'host' para construir la URL del gateway"
            )

        url = f"{self.scheme}://{self.host}"
        if self.port is not None:
            url += f":{self.port}"
        if self.path:
            url += self.path
        return url


@dataclass
class DeviceConfig:
    id: str = "jota-voice"


@dataclass
class OWWConfig:
    host: str = "127.0.0.1"
    port: int = 10401
    wake_words: list[str] = field(default_factory=lambda: ["ok_nabu"])
    reconnect_backoff_s: list[float] = field(default_factory=lambda: [5.0, 10.0, 20.0, 60.0])
    idle_detection_timeout_s: float = 0.0  # 0.0 = sin timeout
    debounce_s: float = 2.0
    backend: str = "wyoming"
    # Tras N reintentos consecutivos fallidos en run_forever, se publica
    # un evento oww_degraded al EventBus (issue #22). Subir este valor en
    # redes muy degradadas para evitar spam al usuario.
    oww_healthcheck_threshold: int = 3


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    frames_per_buffer: int = 512
    preroll_seconds: float = 1.5
    silence_timeout_s: float = 1.5
    recording_timeout_s: float = 15.0
    vad_rms_threshold: float = 200.0
    input_device: int | None = None
    output_device: int | None = None
    backend: str | None = None
    # Reintentos al abrir el stream de captura. PortAudio puede fallar
    # transientemente al abrir un InputStream (device busy, host API no
    # lista, dispositivo recién desconectado). Cada elemento es el delay
    # ANTES de ese intento; la longitud de la lista es también el número
    # MÁXIMO de intentos. Si la lista está vacía, se intenta una sola vez
    # sin reintento. Patrón equivalente a OWWConfig.reconnect_backoff_s
    # (issue #25).
    start_retry_backoff_s: list[float] = field(default_factory=lambda: [0.5, 1.0, 2.0])


# Kiosk de jota-display (server/server.py, POST /state). Única fuente del
# default de la URL http (issue #58: antes la dataclass y el loader tenían
# defaults distintos).
DEFAULT_DISPLAY_HTTP_URL = "http://127.0.0.1:8766"
# Servidor WebSocket local de displays (docs/display-protocol.md). No es el
# 8766 (lo ocupa el kiosk de jota-display en el mismo teléfono) ni el 8765
# (el antiguo ControlServer: configs y procesos viejos durante la migración).
DEFAULT_DISPLAY_WS_PORT = 8767


@dataclass
class DisplayConfig:
    # None -> por plataforma (ver registry.make_display): "http" si hay url
    # explícita (compat con configs desplegadas), "http" en Termux (kiosk),
    # "ws" en macOS/Linux. "null" desactiva el display.
    backend: str | None = None
    # --- backend "http" (compatibilidad con el kiosk de jota-display) ---
    # "" = sin url explícita; el backend http usa DEFAULT_DISPLAY_HTTP_URL.
    url: str = ""
    timeout_s: float = 2.0
    # --- backend "ws" (docs/display-protocol.md) ---
    port: int = DEFAULT_DISPLAY_WS_PORT
    token_path: str | None = None  # None = ~/.jota-voice/display_token
    # Cuota de handshakes con token INVÁLIDO por ventana (issue #106: un
    # cliente con token válido nunca consume ni recibe 429 de este cubo).
    rate_limit_max_requests: int = 10
    rate_limit_window_s: float = 10.0
    max_clients: int = 8


@dataclass
class MenubarConfig:
    enabled: bool = True
    refresh_hz: float = 5.0
    log_path: str | None = None
    config_path: str | None = None

    def __post_init__(self) -> None:
        if self.refresh_hz < 1.0:
            self.refresh_hz = 1.0
        elif self.refresh_hz > 30.0:
            self.refresh_hz = 30.0


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
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    menubar: MenubarConfig = field(default_factory=MenubarConfig)


def _gateway_from_dict(d: dict) -> GatewayConfig:
    if "client_key" not in d:
        raise ValueError("config.yaml: falta client_key en gateway")
    if "url" not in d and "host" not in d:
        raise ValueError("config.yaml: gateway necesita 'url' o 'host'")

    # port y path son OPCIONALES. Si no están en el YAML, no se inyectan
    # en la URL construida (caso típico: gateway detrás de proxy inverso
    # con URL completa del tipo "wss://host/ruta" — el puerto no debe
    # aparecer porque el cliente habla por el esquema por defecto del proxy).
    port = d.get("port")
    if port is not None:
        port = int(port)

    return GatewayConfig(
        client_key=d["client_key"],
        host=d.get("host", ""),
        port=port,
        path=d.get("path", ""),
        scheme=d.get("scheme", "ws"),
        connect_timeout_s=float(d.get("connect_timeout_s", 10.0)),
        url=d.get("url"),
        persistent_session=bool(d.get("persistent_session", True)),
        reconnect_backoff_s=[
            float(x) for x in d.get("reconnect_backoff_s", [1, 2, 5, 10, 30])
        ],
        keepalive_interval_s=float(d.get("keepalive_interval_s", 120.0)),
        ping_interval_s=float(d.get("ping_interval_s", 20.0)),
        ping_timeout_s=float(d.get("ping_timeout_s", 20.0)),
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
        debounce_s=float(d.get("debounce_s", 2.0)),
        oww_healthcheck_threshold=int(d.get("oww_healthcheck_threshold", 3)),
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
        start_retry_backoff_s=[
            float(x) for x in d.get("start_retry_backoff_s", [0.5, 1.0, 2.0])
        ],
    )


def _display_from_dict(d: dict) -> DisplayConfig:
    default = DisplayConfig()
    return DisplayConfig(
        backend=d.get("backend"),
        url=d.get("url") or default.url,
        timeout_s=float(d.get("timeout_s", default.timeout_s)),
        port=int(d.get("port", default.port)),
        token_path=d.get("token_path"),
        rate_limit_max_requests=int(
            d.get("rate_limit_max_requests", default.rate_limit_max_requests)
        ),
        rate_limit_window_s=float(d.get("rate_limit_window_s", default.rate_limit_window_s)),
        max_clients=int(d.get("max_clients", default.max_clients)),
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

    if "control" in data:
        # ControlServer eliminado (Fase D): su `cancel` va ahora por el
        # WebSocket del display (display.port). Se ignora sin fallar.
        logging.getLogger(__name__).warning(
            "config.yaml: la sección 'control' está obsoleta y se ignora "
            "(el cancel externo va por el WebSocket del display, ver "
            "docs/display-protocol.md)"
        )

    menubar_section = data.get("menubar", {})
    if os.environ.get("JOTA_DISABLE_MENUBAR") in ("1", "true", "yes"):
        menubar_section = {**menubar_section, "enabled": False}

    return Config(
        gateway=_gateway_from_dict(data["gateway"]),
        device=_device_from_dict(data["device"]),
        oww=_oww_from_dict(data.get("oww", {})),
        audio=_audio_from_dict(data.get("audio", {})),
        display=_display_from_dict(data.get("display", {})),
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
    print(
        f"  display: backend={cfg.display.backend} url={cfg.display.url!r} "
        f"ws_port={cfg.display.port}"
    )
