from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)


class ConfigError(ValueError):
    """config.yaml inválida. El mensaje nombra cada clave problemática.

    Subclase de ValueError: los llamadores que ya capturaban ValueError de
    load_config siguen funcionando.
    """

    def __init__(self, problems: list[str] | str, path: str | Path | None = None) -> None:
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        self.path = str(path) if path is not None else None
        where = f" ({self.path})" if self.path else ""
        if len(self.problems) == 1:
            msg = f"config.yaml inválida{where}: {self.problems[0]}"
        else:
            msg = f"config.yaml inválida{where}:\n" + "\n".join(
                f"  - {p}" for p in self.problems
            )
        super().__init__(msg)


_MISSING = object()
_TRUE = frozenset({"true", "yes", "on", "1"})
_FALSE = frozenset({"false", "no", "off", "0"})


class _Section:
    """Lee una sección del YAML con tipos estrictos (issue #80).

    Cada lectura inválida se apunta en ``problems`` con el nombre completo de
    la clave (``audio.sample_rate``) y devuelve el default, para poder
    informar de TODOS los errores a la vez. Un valor ``null`` equivale a no
    poner la clave (se usa el default), salvo en los campos que aceptan None.
    """

    def __init__(self, name: str, data: Any, problems: list[str]) -> None:
        self.name = name
        self.problems = problems
        if data is None:
            data = {}
        if not isinstance(data, dict):
            problems.append(
                f"{name}: debe ser una sección (clave: valor), no {type(data).__name__}"
            )
            data = {}
        self.data: dict = data

    def _bad(self, key: str, value: Any, expected: str) -> None:
        self.problems.append(f"{self.name}.{key}: {expected} (recibido {value!r})")

    def has(self, key: str) -> bool:
        return self.data.get(key) is not None

    def raw(self, key: str, default: Any = None) -> Any:
        value = self.data.get(key)
        return default if value is None else value

    def _check_range(self, key, value, *, min=None, max=None, gt=None) -> bool:
        if gt is not None and not value > gt:
            self._bad(key, value, f"debe ser mayor que {gt}")
            return False
        if min is not None and value < min:
            self._bad(key, value, f"debe ser como mínimo {min}")
            return False
        if max is not None and value > max:
            self._bad(key, value, f"debe ser como máximo {max}")
            return False
        return True

    def _to_int(self, key: str, value: Any) -> Any:
        if isinstance(value, bool):
            return _MISSING
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return _MISSING
        return _MISSING

    def _to_float(self, key: str, value: Any) -> Any:
        if isinstance(value, bool):
            return _MISSING
        if isinstance(value, (int, float)):
            result = float(value)
        elif isinstance(value, str):
            try:
                result = float(value.strip())
            except ValueError:
                return _MISSING
        else:
            return _MISSING
        return result if math.isfinite(result) else _MISSING

    def get_int(self, key: str, default: Any, *, optional: bool = False, **rng) -> Any:
        value = self.data.get(key)
        if value is None:
            return None if optional else default
        result = self._to_int(key, value)
        if result is _MISSING:
            self._bad(key, value, "debe ser un número entero")
            return default
        return result if self._check_range(key, result, **rng) else default

    def get_float(self, key: str, default: float, **rng) -> float:
        value = self.data.get(key)
        if value is None:
            return default
        result = self._to_float(key, value)
        if result is _MISSING:
            self._bad(key, value, "debe ser un número")
            return default
        return result if self._check_range(key, result, **rng) else default

    def get_float_list(self, key: str, default: list[float], *, non_empty: bool = False,
                   **rng) -> list[float]:
        value = self.data.get(key)
        if value is None:
            return list(default)
        if not isinstance(value, list):
            self._bad(key, value, "debe ser una lista de números, p. ej. [1, 2, 5]")
            return list(default)
        out: list[float] = []
        for item in value:
            num = self._to_float(key, item)
            if num is _MISSING:
                self._bad(key, value, "debe ser una lista de números, p. ej. [1, 2, 5]")
                return list(default)
            if not self._check_range(key, num, **rng):
                return list(default)
            out.append(num)
        if non_empty and not out:
            self._bad(key, value, "no puede ser una lista vacía")
            return list(default)
        return out

    def get_bool(self, key: str, default: bool) -> bool:
        value = self.data.get(key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            low = value.strip().lower()
            if low in _TRUE:
                return True
            if low in _FALSE:
                return False
        self._bad(key, value, "debe ser true o false")
        return default

    def get_str(self, key: str, default: Any, *, optional: bool = False,
            choices: tuple[str, ...] | None = None, non_empty: bool = False) -> Any:
        value = self.data.get(key)
        if value is None:
            return None if optional else default
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            self._bad(key, value, "debe ser un texto")
            return default
        text = str(value) if not isinstance(value, str) else value
        if non_empty and not text.strip():
            self._bad(key, value, "no puede estar vacío")
            return default
        if choices is not None and text not in choices:
            opts = ", ".join(repr(c) for c in choices)
            self._bad(key, value, f"debe ser uno de {opts}")
            return default
        return text


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


def _start(problems: list[str] | None) -> tuple[list[str], bool]:
    """Los loaders acumulan en la lista de load_config; llamados sueltos
    (tests) lanzan ellos mismos."""
    return ([], True) if problems is None else (problems, False)


def _finish(problems: list[str], own: bool) -> None:
    if own and problems:
        raise ConfigError(problems)


def _gateway_from_dict(d: Any, problems: list[str] | None = None) -> GatewayConfig:
    problems, own = _start(problems)
    s = _Section("gateway", d, problems)
    default = GatewayConfig(client_key="")
    if "client_key" not in s.data:
        problems.append("gateway.client_key: falta (obligatoria)")
    if not s.has("url") and not s.has("host"):
        problems.append("gateway: necesita 'url' o 'host'")

    # port y path son OPCIONALES. Si no están en el YAML, no se inyectan
    # en la URL construida (caso típico: gateway detrás de proxy inverso
    # con URL completa del tipo "wss://host/ruta" — el puerto no debe
    # aparecer porque el cliente habla por el esquema por defecto del proxy).
    cfg = GatewayConfig(
        client_key=s.get_str("client_key", ""),
        host=s.get_str("host", ""),
        port=s.get_int("port", None, optional=True),
        path=s.get_str("path", ""),
        scheme=s.get_str("scheme", default.scheme),
        connect_timeout_s=s.get_float("connect_timeout_s", default.connect_timeout_s),
        url=s.get_str("url", None, optional=True),
        persistent_session=s.get_bool("persistent_session", default.persistent_session),
        reconnect_backoff_s=s.get_float_list("reconnect_backoff_s", default.reconnect_backoff_s),
        keepalive_interval_s=s.get_float("keepalive_interval_s", default.keepalive_interval_s),
        ping_interval_s=s.get_float("ping_interval_s", default.ping_interval_s),
        ping_timeout_s=s.get_float("ping_timeout_s", default.ping_timeout_s),
    )
    _finish(problems, own)
    return cfg


def _device_from_dict(d: Any, problems: list[str] | None = None) -> DeviceConfig:
    problems, own = _start(problems)
    s = _Section("device", d, problems)
    if "id" not in s.data:
        problems.append("device.id: falta (obligatorio)")
    cfg = DeviceConfig(id=s.get_str("id", DeviceConfig().id))
    _finish(problems, own)
    return cfg


def _oww_from_dict(d: Any, problems: list[str] | None = None) -> OWWConfig:
    problems, own = _start(problems)
    s = _Section("oww", d, problems)
    default = OWWConfig()
    wake_words = s.raw("wake_words", default.wake_words)
    if isinstance(wake_words, str):
        wake_words = [wake_words]  # antes list("ok_nabu") daba una lista de letras
    if not isinstance(wake_words, list):
        s._bad("wake_words", wake_words, "debe ser una lista de nombres")
        wake_words = list(default.wake_words)
    cfg = OWWConfig(
        backend=s.get_str("backend", default.backend),
        host=s.get_str("host", default.host),
        port=s.get_int("port", default.port),
        wake_words=[str(w) for w in wake_words],
        reconnect_backoff_s=s.get_float_list("reconnect_backoff_s", default.reconnect_backoff_s),
        idle_detection_timeout_s=s.get_float(
            "idle_detection_timeout_s", default.idle_detection_timeout_s
        ),
        debounce_s=s.get_float("debounce_s", default.debounce_s),
        oww_healthcheck_threshold=s.get_int(
            "oww_healthcheck_threshold", default.oww_healthcheck_threshold
        ),
    )
    _finish(problems, own)
    return cfg


def _device_index(s: _Section, key: str) -> int | str | None:
    """sounddevice acepta un índice entero o un nombre de dispositivo."""
    value = s.data.get(key)
    if value is None or isinstance(value, str):
        return value
    return s.get_int(key, None, optional=True)


def _audio_from_dict(d: Any, problems: list[str] | None = None) -> AudioConfig:
    problems, own = _start(problems)
    s = _Section("audio", d, problems)
    default = AudioConfig()
    cfg = AudioConfig(
        backend=s.get_str("backend", None, optional=True),
        sample_rate=s.get_int("sample_rate", default.sample_rate),
        channels=s.get_int("channels", default.channels),
        frames_per_buffer=s.get_int("frames_per_buffer", default.frames_per_buffer),
        preroll_seconds=s.get_float("preroll_seconds", default.preroll_seconds),
        silence_timeout_s=s.get_float("silence_timeout_s", default.silence_timeout_s),
        recording_timeout_s=s.get_float("recording_timeout_s", default.recording_timeout_s),
        vad_rms_threshold=s.get_float("vad_rms_threshold", default.vad_rms_threshold),
        input_device=_device_index(s, "input_device"),
        output_device=_device_index(s, "output_device"),
        start_retry_backoff_s=s.get_float_list(
            "start_retry_backoff_s", default.start_retry_backoff_s
        ),
    )
    _finish(problems, own)
    return cfg


def _display_from_dict(d: Any, problems: list[str] | None = None) -> DisplayConfig:
    problems, own = _start(problems)
    s = _Section("display", d, problems)
    default = DisplayConfig()
    cfg = DisplayConfig(
        backend=s.get_str("backend", None, optional=True),
        url=s.get_str("url", default.url),
        timeout_s=s.get_float("timeout_s", default.timeout_s),
        port=s.get_int("port", default.port),
        token_path=s.get_str("token_path", None, optional=True),
        rate_limit_max_requests=s.get_int(
            "rate_limit_max_requests", default.rate_limit_max_requests
        ),
        rate_limit_window_s=s.get_float("rate_limit_window_s", default.rate_limit_window_s),
        max_clients=s.get_int("max_clients", default.max_clients),
    )
    _finish(problems, own)
    return cfg


def _logging_from_dict(d: Any, problems: list[str] | None = None) -> LoggingConfig:
    problems, own = _start(problems)
    s = _Section("logging", d, problems)
    cfg = LoggingConfig(level=s.get_str("level", LoggingConfig().level))
    _finish(problems, own)
    return cfg


def _menubar_from_dict(d: Any, problems: list[str] | None = None) -> MenubarConfig:
    problems, own = _start(problems)
    s = _Section("menubar", d, problems)
    default = MenubarConfig()
    cfg = MenubarConfig(
        enabled=s.get_bool("enabled", default.enabled),
        refresh_hz=s.get_float("refresh_hz", default.refresh_hz),
        log_path=s.get_str("log_path", None, optional=True),
        config_path=s.get_str("config_path", None, optional=True),
    )
    _finish(problems, own)
    return cfg


def load_config(path: str | Path) -> Config:
    """Carga y valida config.yaml. Lanza ConfigError con TODOS los problemas
    encontrados (cada uno con el nombre completo de la clave)."""
    real_path = Path(path).resolve()
    _load_env_file(real_path.parent / ".env")
    with open(path) as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError("el fichero debe contener secciones (gateway:, device:, ...)", path)

    problems: list[str] = []
    for required in ("gateway", "device"):
        if required not in data:
            problems.append(f"{required}: sección obligatoria")

    if "control" in data:
        # ControlServer eliminado (Fase D): su `cancel` va ahora por el
        # WebSocket del display (display.port). Se ignora sin fallar.
        log.warning(
            "config.yaml: la sección 'control' está obsoleta y se ignora "
            "(el cancel externo va por el WebSocket del display, ver "
            "docs/display-protocol.md)"
        )

    menubar_section = data.get("menubar")
    if os.environ.get("JOTA_DISABLE_MENUBAR") in ("1", "true", "yes"):
        base = menubar_section if isinstance(menubar_section, dict) else {}
        menubar_section = {**base, "enabled": False}

    cfg = Config(
        # Si falta la sección entera basta con ese error, sin repetir sus
        # claves obligatorias una a una.
        gateway=_gateway_from_dict(data.get("gateway"), problems if "gateway" in data else []),
        device=_device_from_dict(data.get("device"), problems if "device" in data else []),
        oww=_oww_from_dict(data.get("oww"), problems),
        audio=_audio_from_dict(data.get("audio"), problems),
        display=_display_from_dict(data.get("display"), problems),
        logging=_logging_from_dict(data.get("logging"), problems),
        menubar=_menubar_from_dict(menubar_section, problems),
    )
    if problems:
        raise ConfigError(problems, path)
    return cfg


if __name__ == "__main__":
    import sys
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    try:
        cfg = load_config(cfg_path)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        sys.exit(2)
    print("Config cargada OK")
    print(f"  device:  {cfg.device.id}")
    print(f"  gateway: {cfg.gateway.ws_url}")
    print(f"  oww:     {cfg.oww.host}:{cfg.oww.port} (backend={cfg.oww.backend})")
    print(f"  audio:   backend={cfg.audio.backend}")
    print(
        f"  display: backend={cfg.display.backend} url={cfg.display.url!r} "
        f"ws_port={cfg.display.port}"
    )
