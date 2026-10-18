"""Tests de config.py."""
from __future__ import annotations

import os
import tempfile

import pytest
import yaml


def _write_cfg(data: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(data, f)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# Sección display (Fase D: backend ws + http de compatibilidad, issue #58)
# ---------------------------------------------------------------------------

_BASE = {"gateway": {"host": "127.0.0.1", "client_key": "test"}, "device": {"id": "test-device"}}


def _load(extra: dict):
    from config import load_config
    path = _write_cfg({**_BASE, **extra})
    try:
        return load_config(path)
    finally:
        os.unlink(path)


def test_display_defaults_sin_seccion() -> None:
    from config import DEFAULT_DISPLAY_WS_PORT, DisplayConfig
    cfg = _load({})
    assert cfg.display.backend is None
    assert cfg.display.url == ""
    assert cfg.display.port == DEFAULT_DISPLAY_WS_PORT == 8767
    assert cfg.display.token_path is None
    assert cfg.display.rate_limit_max_requests == 10
    assert cfg.display.rate_limit_window_s == 10.0
    assert cfg.display.max_clients == 8
    # Issue #58: el default de la dataclass y el del loader son el mismo.
    assert DisplayConfig() == cfg.display


def test_display_campos_ws_custom() -> None:
    cfg = _load({"display": {
        "backend": "ws", "port": 9100, "token_path": "/tmp/tok",
        "rate_limit_max_requests": 3, "rate_limit_window_s": 5,
        "max_clients": 2,
    }})
    assert cfg.display.backend == "ws"
    assert cfg.display.port == 9100
    assert cfg.display.token_path == "/tmp/tok"
    assert cfg.display.rate_limit_max_requests == 3
    assert cfg.display.rate_limit_window_s == 5.0
    assert cfg.display.max_clients == 2


def test_display_http_campos() -> None:
    cfg = _load({"display": {"backend": "http", "url": "http://127.0.0.1:8766", "timeout_s": 1}})
    assert cfg.display.backend == "http"
    assert cfg.display.url == "http://127.0.0.1:8766"
    assert cfg.display.timeout_s == 1.0


def test_seccion_control_obsoleta_se_ignora() -> None:
    """Configs desplegadas aún llevan `control: port: 8765` (el ControlServer
    se eliminó en la Fase D: su cancel va por el WebSocket del display)."""
    cfg = _load({"control": {"port": 8765, "token_path": "/tmp/x"}})
    assert not hasattr(cfg, "control")


def test_display_url_null_equivale_a_sin_url() -> None:
    cfg = _load({"display": {"backend": None, "url": None}})
    assert cfg.display.url == ""


def test_ws_url_prioriza_url_sobre_host_port_path() -> None:
    """Si el usuario da 'url' completa, se respeta LITERAL — sin inyectar
    scheme, puerto ni path por defecto. Caso típico: gateway detrás de
    Cloudflare Tunnel / nginx con TLS donde la URL ya viene 'wss://host/ruta'."""
    from config import GatewayConfig
    cfg = GatewayConfig(client_key="x", url="wss://foo.example.com/api/gw/ws/stream")
    assert cfg.ws_url == "wss://foo.example.com/api/gw/ws/stream"


def test_ws_url_sin_url_construye_desde_host_port_path() -> None:
    from config import GatewayConfig
    cfg = GatewayConfig(client_key="x", host="myhost", port=1234, path="/p")
    assert cfg.ws_url == "ws://myhost:1234/p"


def test_ws_url_sin_port_no_inyecta_puerto() -> None:
    """Si el usuario da host sin port, NO se añade ':8004' ni nada por defecto.
    Esto es lo que pide el usuario del Mac: una URL limpia sin puerto inyectado."""
    from config import GatewayConfig
    cfg = GatewayConfig(client_key="x", host="green-house.local", path="/api/gateway/ws/stream", scheme="wss")
    assert cfg.ws_url == "wss://green-house.local/api/gateway/ws/stream"


def test_ws_url_sin_path_no_inyecta_path() -> None:
    """Si el usuario da host sin path, NO se añade '/ws/stream' por defecto."""
    from config import GatewayConfig
    cfg = GatewayConfig(client_key="x", host="green-house.local", port=8004)
    assert cfg.ws_url == "ws://green-house.local:8004"


def test_ws_url_sin_nada_solo_host() -> None:
    """Mínimo absoluto: solo host. Sin scheme inyectado raro, sin path, sin puerto."""
    from config import GatewayConfig
    cfg = GatewayConfig(client_key="x", host="green-house.local")
    assert cfg.ws_url == "ws://green-house.local"


def test_ws_url_falla_si_no_hay_url_ni_host() -> None:
    """Si no hay ni url ni host, no se puede construir la URL — error claro."""
    from config import GatewayConfig
    import pytest
    cfg = GatewayConfig(client_key="x")
    with pytest.raises(ValueError, match="falta 'url' o 'host'"):
        _ = cfg.ws_url


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


def test_load_config_sin_port_no_inyecta_puerto() -> None:
    """Carga un config con host y scheme pero sin port — el ws_url resultante
    NO debe contener ':8004' ni ningún otro puerto por defecto."""
    path = _write_cfg({
        "gateway": {
            "host": "green-house.local",
            "scheme": "wss",
            "path": "/api/gateway/ws/stream",
            "client_key": "test",
        },
        "device": {"id": "test-device"},
    })
    try:
        from config import load_config
        cfg = load_config(path)
        assert cfg.gateway.ws_url == "wss://green-house.local/api/gateway/ws/stream"
        assert ":" not in cfg.gateway.ws_url.replace("://", "", 1).split("/")[0] or \
               cfg.gateway.ws_url.count(":") == 0
    finally:
        os.unlink(path)


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


def test_load_config_carga_env_sibling_si_existe(monkeypatch) -> None:
    # setenv + delenv: delenv de una variable ausente no registra deshacer, y
    # el setdefault de load_config se quedaría en os.environ para el resto de
    # la suite (#39).
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "x")
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID")
    with tempfile.TemporaryDirectory() as workdir:
        cfg_path = os.path.join(workdir, "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(
                {"gateway": {"host": "127.0.0.1", "client_key": "test"}, "device": {"id": "test-device"}},
                f,
            )
        with open(os.path.join(workdir, ".env"), "w") as f:
            f.write("CF_ACCESS_CLIENT_ID=from-dotenv\n")

        from config import load_config
        load_config(cfg_path)
        assert os.environ.get("CF_ACCESS_CLIENT_ID") == "from-dotenv"


def test_load_config_no_pisa_variable_de_entorno_ya_definida(monkeypatch) -> None:
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "ya-estaba")
    with tempfile.TemporaryDirectory() as workdir:
        cfg_path = os.path.join(workdir, "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(
                {"gateway": {"host": "127.0.0.1", "client_key": "test"}, "device": {"id": "test-device"}},
                f,
            )
        with open(os.path.join(workdir, ".env"), "w") as f:
            f.write("CF_ACCESS_CLIENT_ID=from-dotenv\n")

        from config import load_config
        load_config(cfg_path)
        assert os.environ.get("CF_ACCESS_CLIENT_ID") == "ya-estaba"


def _env_from(tmp_path, text: str, environ: dict | None = None) -> dict:
    from config import _load_env_file
    path = tmp_path / ".env"
    path.write_text(text)
    env = {} if environ is None else environ
    _load_env_file(path, environ=env)
    return env


@pytest.mark.parametrize("line,expected", [
    ('GATEWAY_CLIENT_KEY="abc123"', "abc123"),
    ("GATEWAY_CLIENT_KEY='abc123'", "abc123"),
    ("GATEWAY_CLIENT_KEY=abc123", "abc123"),
    ("GATEWAY_CLIENT_KEY=wss://host/x  # comentario", "wss://host/x"),
    ('GATEWAY_CLIENT_KEY="abc123"   # comentario', "abc123"),
    ('GATEWAY_CLIENT_KEY="a # dentro de comillas"', "a # dentro de comillas"),
    ("GATEWAY_CLIENT_KEY=se#creto", "se#creto"),
    ("export GATEWAY_CLIENT_KEY=abc123", "abc123"),
    ("  GATEWAY_CLIENT_KEY = abc123  ", "abc123"),
    ("GATEWAY_CLIENT_KEY=", ""),
    ('GATEWAY_CLIENT_KEY=""', ""),
    ("GATEWAY_CLIENT_KEY=a=b", "a=b"),
])
def test_env_file_quita_comillas_y_comentarios_inline(tmp_path, line, expected) -> None:
    """Issue #51: mismo tratamiento que _cfg_val() del smoke-test de shell."""
    env = _env_from(tmp_path, line + "\n")
    assert env == {"GATEWAY_CLIENT_KEY": expected}


def test_env_file_ignora_comentarios_y_lineas_invalidas(tmp_path) -> None:
    env = _env_from(tmp_path, "# comentario\n\nSIN_IGUAL\n=sin_clave\nA=1\n")
    assert env == {"A": "1"}


def test_env_file_no_pisa_lo_ya_definido(tmp_path) -> None:
    env = _env_from(tmp_path, "A=del-fichero\nB=nuevo\n", {"A": "ya-estaba"})
    assert env == {"A": "ya-estaba", "B": "nuevo"}


def test_menubar_default_enabled() -> None:
    path = _write_cfg({"gateway": {"host": "127.0.0.1", "client_key": "test"}, "device": {"id": "test-device"}})
    try:
        from config import MenubarConfig, load_config
        cfg = load_config(path)
        assert isinstance(cfg.menubar, MenubarConfig)
        assert cfg.menubar.enabled is True
        assert cfg.menubar.refresh_hz == 5.0
        assert cfg.menubar.log_path is None
        assert cfg.menubar.config_path is None
    finally:
        os.unlink(path)


def test_menubar_env_disable(monkeypatch) -> None:
    monkeypatch.setenv("JOTA_DISABLE_MENUBAR", "1")
    path = _write_cfg({"gateway": {"host": "127.0.0.1", "client_key": "test"}, "device": {"id": "test-device"}})
    try:
        from config import load_config
        cfg = load_config(path)
        assert cfg.menubar.enabled is False
    finally:
        os.unlink(path)


def test_menubar_yaml_override() -> None:
    path = _write_cfg({
        "gateway": {"host": "127.0.0.1", "client_key": "test"},
        "device": {"id": "test-device"},
        "menubar": {"enabled": False, "refresh_hz": 10.0},
    })
    try:
        from config import load_config
        cfg = load_config(path)
        assert cfg.menubar.enabled is False
        assert cfg.menubar.refresh_hz == 10.0
    finally:
        os.unlink(path)


def test_menubar_refresh_clamped_low() -> None:
    from config import MenubarConfig
    cfg = MenubarConfig(refresh_hz=0.1)
    assert cfg.refresh_hz == 1.0


def test_menubar_refresh_clamped_high() -> None:
    from config import MenubarConfig
    cfg = MenubarConfig(refresh_hz=999.0)
    assert cfg.refresh_hz == 30.0


def test_gateway_config_persistent_session_default_true() -> None:
    """Issue #24: gateway.persistent_session default True → handshake eager."""
    from config import _gateway_from_dict
    cfg = _gateway_from_dict({"client_key": "x", "host": "127.0.0.1"})
    assert cfg.persistent_session is True


def test_gateway_config_persistent_session_override_false() -> None:
    """Issue #24: gateway.persistent_session: false en YAML → handshake por turno."""
    from config import _gateway_from_dict
    cfg = _gateway_from_dict({
        "client_key": "x", "host": "127.0.0.1",
        "persistent_session": False,
    })
    assert cfg.persistent_session is False


def test_gateway_config_persistent_session_explicit_true() -> None:
    """Issue #24: gateway.persistent_session: true explícito se respeta."""
    from config import _gateway_from_dict
    cfg = _gateway_from_dict({
        "client_key": "x", "host": "127.0.0.1",
        "persistent_session": True,
    })
    assert cfg.persistent_session is True


# ---------------------------------------------------------------------------
# Issue #80: un valor con tipo inválido da un ConfigError que nombra la clave
# ---------------------------------------------------------------------------

_NUMERIC_FIELDS = [
    ("gateway", "port"),
    ("gateway", "connect_timeout_s"),
    ("gateway", "keepalive_interval_s"),
    ("gateway", "ping_interval_s"),
    ("gateway", "ping_timeout_s"),
    ("oww", "port"),
    ("oww", "debounce_s"),
    ("oww", "idle_detection_timeout_s"),
    ("oww", "oww_healthcheck_threshold"),
    ("audio", "sample_rate"),
    ("audio", "channels"),
    ("audio", "frames_per_buffer"),
    ("audio", "preroll_seconds"),
    ("audio", "silence_timeout_s"),
    ("audio", "recording_timeout_s"),
    ("audio", "vad_rms_threshold"),
    ("display", "timeout_s"),
    ("display", "port"),
    ("display", "rate_limit_max_requests"),
    ("display", "rate_limit_window_s"),
    ("display", "max_clients"),
    ("menubar", "refresh_hz"),
]


def _load_tmp(tmp_path, data):
    from config import load_config
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_config(path)


def _with(section: str, values: dict) -> dict:
    data = {k: dict(v) for k, v in _BASE.items()}
    data[section] = {**data.get(section, {}), **values}
    return data


@pytest.mark.parametrize("section,key", _NUMERIC_FIELDS)
def test_valor_no_numerico_da_config_error_con_la_clave(tmp_path, section, key) -> None:
    from config import ConfigError
    with pytest.raises(ConfigError) as info:
        _load_tmp(tmp_path, _with(section, {key: "abc"}))
    msg = str(info.value)
    assert f"{section}.{key}" in msg
    assert "'abc'" in msg
    assert str(tmp_path / "config.yaml") in msg


@pytest.mark.parametrize("section", ["gateway", "oww"])
def test_lista_de_numeros_invalida_nombra_la_clave(tmp_path, section) -> None:
    from config import ConfigError
    with pytest.raises(ConfigError, match=rf"{section}\.reconnect_backoff_s"):
        _load_tmp(tmp_path, _with(section, {"reconnect_backoff_s": [1, "x"]}))


def test_config_error_es_value_error() -> None:
    from config import ConfigError
    assert issubclass(ConfigError, ValueError)


def test_el_error_lista_todos_los_campos_invalidos_a_la_vez(tmp_path) -> None:
    from config import ConfigError
    data = _with("audio", {"sample_rate": "x", "channels": "y"})
    data["oww"] = {"port": "z"}
    with pytest.raises(ConfigError) as info:
        _load_tmp(tmp_path, data)
    msg = str(info.value)
    assert "audio.sample_rate" in msg and "audio.channels" in msg and "oww.port" in msg


def test_booleanos_no_se_evaluan_por_truthiness(tmp_path) -> None:
    """bool("false") es True en Python: antes `persistent_session: "false"`
    activaba la sesión persistente."""
    cfg = _load_tmp(tmp_path, _with("gateway", {"persistent_session": "false"}))
    assert cfg.gateway.persistent_session is False
    cfg = _load_tmp(tmp_path, _with("menubar", {"enabled": "no"}))
    assert cfg.menubar.enabled is False


def test_booleano_invalido_nombra_la_clave(tmp_path) -> None:
    from config import ConfigError
    with pytest.raises(ConfigError, match=r"gateway\.persistent_session"):
        _load_tmp(tmp_path, _with("gateway", {"persistent_session": "quizá"}))


def test_numeros_entre_comillas_siguen_valiendo(tmp_path) -> None:
    """Configs desplegadas pueden llevar números entrecomillados."""
    cfg = _load_tmp(tmp_path, _with("gateway", {"port": "8004", "connect_timeout_s": "7.5"}))
    assert cfg.gateway.port == 8004
    assert cfg.gateway.connect_timeout_s == 7.5


def test_entero_no_acepta_bool_ni_decimales(tmp_path) -> None:
    from config import ConfigError
    with pytest.raises(ConfigError, match=r"oww\.port"):
        _load_tmp(tmp_path, _with("oww", {"port": True}))
    with pytest.raises(ConfigError, match=r"audio\.channels"):
        _load_tmp(tmp_path, _with("audio", {"channels": 1.5}))


def test_seccion_que_no_es_un_mapa_da_config_error(tmp_path) -> None:
    from config import ConfigError
    with pytest.raises(ConfigError, match="audio"):
        _load_tmp(tmp_path, {**_BASE, "audio": [1, 2]})


def test_seccion_vacia_usa_los_defaults(tmp_path) -> None:
    """`oww:` sin nada debajo es None en YAML: equivale a no ponerla."""
    from config import OWWConfig
    cfg = _load_tmp(tmp_path, {**_BASE, "oww": None})
    assert cfg.oww == OWWConfig()


def test_fichero_vacio_da_config_error(tmp_path) -> None:
    from config import ConfigError, load_config
    path = tmp_path / "config.yaml"
    path.write_text("")
    with pytest.raises(ConfigError, match="gateway"):
        load_config(path)


# ---------------------------------------------------------------------------
# Issue #49: rangos, valores permitidos y claves desconocidas
# ---------------------------------------------------------------------------

_OUT_OF_RANGE = [
    ("gateway", "port", 99999),
    ("gateway", "port", 0),
    ("gateway", "connect_timeout_s", 0),
    ("gateway", "keepalive_interval_s", -1),
    ("gateway", "ping_interval_s", -1),
    ("gateway", "ping_timeout_s", -1),
    ("gateway", "reconnect_backoff_s", [1, -2]),
    ("gateway", "scheme", "http"),
    ("gateway", "url", "http://host/ws"),
    ("oww", "port", 70000),
    ("oww", "backend", "porcupine"),
    ("oww", "debounce_s", -0.5),
    ("oww", "idle_detection_timeout_s", -1),
    ("oww", "oww_healthcheck_threshold", 0),
    ("oww", "reconnect_backoff_s", []),
    ("oww", "wake_words", []),
    ("oww", "host", ""),
    ("audio", "backend", "alsa"),
    ("audio", "sample_rate", 0),
    ("audio", "channels", 0),
    ("audio", "frames_per_buffer", 0),
    ("audio", "preroll_seconds", -1),
    ("audio", "silence_timeout_s", 0),
    ("audio", "recording_timeout_s", 0),
    ("audio", "vad_rms_threshold", -50),
    ("audio", "input_device", -1),
    ("audio", "start_retry_backoff_s", [-1]),
    ("display", "backend", "kiosk"),
    ("display", "timeout_s", 0),
    ("display", "port", 65536),
    ("display", "rate_limit_max_requests", 0),
    ("display", "rate_limit_window_s", 0),
    ("display", "max_clients", 0),
    ("logging", "level", "VERBOSE"),
    ("device", "id", ""),
]


@pytest.mark.parametrize("section,key,value", _OUT_OF_RANGE)
def test_valor_fuera_de_rango_da_config_error_con_la_clave(tmp_path, section, key, value) -> None:
    from config import ConfigError
    with pytest.raises(ConfigError) as info:
        _load_tmp(tmp_path, _with(section, {key: value}))
    assert f"{section}.{key}" in str(info.value)


@pytest.mark.parametrize("value", ["", "   ", None])
def test_client_key_vacia_o_nula_falla_al_cargar(tmp_path, value) -> None:
    """Antes `client_key: ""` o `null` pasaban y fallaban mucho después, al
    autenticar, con un error de protocolo confuso."""
    from config import ConfigError
    with pytest.raises(ConfigError, match=r"gateway\.client_key"):
        _load_tmp(tmp_path, _with("gateway", {"client_key": value}))


def test_client_key_numerica_se_acepta_como_texto(tmp_path) -> None:
    cfg = _load_tmp(tmp_path, _with("gateway", {"client_key": 12345}))
    assert cfg.gateway.client_key == "12345"


def test_clave_desconocida_avisa_sin_fallar(tmp_path, caplog) -> None:
    """Una clave mal escrita (typo) aplicaba el default en silencio."""
    data = _with("audio", {"silence_timout_s": 3})
    data["audoi"] = {"x": 1}
    with caplog.at_level("WARNING", logger="config"):
        cfg = _load_tmp(tmp_path, data)
    assert cfg.audio.silence_timeout_s == 1.5
    assert "audio.silence_timout_s" in caplog.text
    assert "audoi" in caplog.text


def test_logging_level_se_normaliza(tmp_path) -> None:
    cfg = _load_tmp(tmp_path, {**_BASE, "logging": {"level": "debug"}})
    assert cfg.logging.level == "DEBUG"


@pytest.mark.parametrize("value", [0, 4.5, "0"])
def test_ceros_validos_se_aceptan(tmp_path, value) -> None:
    """0 desactiva keepalive y ping (documentado en config.example.yaml)."""
    cfg = _load_tmp(tmp_path, _with("gateway", {
        "keepalive_interval_s": value, "ping_interval_s": value, "ping_timeout_s": value,
    }))
    assert cfg.gateway.keepalive_interval_s == float(value)


def test_keepalive_mayor_que_idle_timeout_del_gateway_avisa(tmp_path, caplog) -> None:
    with caplog.at_level("WARNING", logger="config"):
        _load_tmp(tmp_path, _with("gateway", {"keepalive_interval_s": 400}))
    assert "gateway.keepalive_interval_s" in caplog.text


def test_audio_device_acepta_indice_o_nombre(tmp_path) -> None:
    cfg = _load_tmp(tmp_path, _with("audio", {"input_device": 2, "output_device": "MacBook Pro Speakers"}))
    assert cfg.audio.input_device == 2
    assert cfg.audio.output_device == "MacBook Pro Speakers"


def test_display_backend_null_con_url_sigue_valiendo(tmp_path) -> None:
    """Configs desplegadas (Termux): `backend: "null"` o null con `url:`."""
    cfg = _load_tmp(tmp_path, {**_BASE, "display": {"backend": "null", "url": "http://127.0.0.1:8766"}})
    assert cfg.display.backend == "null"
    cfg = _load_tmp(tmp_path, {**_BASE, "display": {"backend": None, "url": "http://127.0.0.1:8766"}})
    assert cfg.display.backend is None


def test_seccion_control_obsoleta_solo_avisa(tmp_path, caplog) -> None:
    with caplog.at_level("WARNING", logger="config"):
        _load_tmp(tmp_path, {**_BASE, "control": {"port": 8765, "token_path": "/tmp/x"}})
    assert "control" in caplog.text
    # Un solo aviso: sus claves internas no se reportan como desconocidas.
    assert "control.port" not in caplog.text


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _wizard_config_yaml() -> str:
    """El YAML que escribe install/macos/02-config-wizard.sh, con valores reales."""
    with open(os.path.join(_REPO_ROOT, "install/macos/02-config-wizard.sh")) as f:
        script = f.read()
    body = script.split('cat > "$CFG" <<EOF\n', 1)[1].split("\nEOF\n", 1)[0]
    return (
        body.replace("${GATEWAY_URL}", "wss://gw.example.org/ws/stream")
        .replace("${GATEWAY_CLIENT_KEY}", "clave-real")
        .replace("${ID}", "macbook")
        .replace("${WAKE_WORD}", "ok_nabu")
    )


def test_seccion_hosts_es_del_lado_shell_y_no_avisa(tmp_path, caplog) -> None:
    """Issue #79: `hosts:` la leen los scripts de Termux (lib/yaml.sh) para
    /etc/hosts; el cliente Python la reconoce y la ignora sin aviso."""
    with caplog.at_level("WARNING", logger="config"):
        cfg = _load_tmp(tmp_path, {**_BASE, "hosts": [{"ip": "192.168.1.2", "name": "srv"}]})
    assert not caplog.records, caplog.text
    assert not hasattr(cfg, "hosts")


def test_config_example_documenta_hosts_como_solo_shell() -> None:
    with open(os.path.join(_REPO_ROOT, "config.example.yaml")) as f:
        text = f.read()
    lines = text.split("\nhosts:", 1)[0].splitlines()
    comment: list[str] = []
    while lines and lines[-1].startswith("#"):
        comment.insert(0, lines.pop())
    block = "\n".join(comment).lower()
    assert "solo" in block and "termux" in block and "python" in block, block


# ---------------------------------------------------------------------------
# Issue #50: placeholders sin rellenar (como el grep de install.sh)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("section,key,value", [
    ("gateway", "client_key", "RELLENAR"),
    ("gateway", "client_key", "YOUR_KEY"),
    ("gateway", "client_key", "CHANGE-ME"),
    ("gateway", "url", "wss://CHANGE-ME.example.com/api/gateway/ws/stream"),
    ("gateway", "host", "YOUR_HOST.local"),
    ("device", "id", "RELLENAR"),
    ("oww", "host", "CHANGE_ME"),
])
def test_placeholder_sin_rellenar_falla_al_cargar(tmp_path, section, key, value) -> None:
    from config import ConfigError
    data = _with(section, {key: value})
    if key == "url":
        data["gateway"].pop("host")
    with pytest.raises(ConfigError) as info:
        _load_tmp(tmp_path, data)
    msg = str(info.value)
    assert f"{section}.{key}" in msg
    assert "sin rellenar" in msg


def test_config_example_sin_rellenar_falla_nombrando_las_claves(tmp_path) -> None:
    from config import ConfigError, load_config
    path = tmp_path / "config.yaml"
    with open(os.path.join(_REPO_ROOT, "config.example.yaml")) as f:
        path.write_text(f.read())
    with pytest.raises(ConfigError) as info:
        load_config(path)
    msg = str(info.value)
    assert "gateway.client_key" in msg and "gateway.url" in msg


def test_config_example_rellena_carga_sin_avisos(tmp_path, caplog) -> None:
    """El ejemplo, con sus placeholders rellenos, es una config válida y sin
    claves desconocidas (hosts: incluida)."""
    from config import load_config
    with open(os.path.join(_REPO_ROOT, "config.example.yaml")) as f:
        text = f.read()
    text = text.replace("CHANGE-ME", "gw").replace("RELLENAR", "clave-real")
    path = tmp_path / "config.yaml"
    path.write_text(text)
    with caplog.at_level("WARNING", logger="config"):
        cfg = load_config(path)
    assert cfg.gateway.client_key == "clave-real"
    assert not caplog.records, caplog.text


def test_la_config_del_wizard_de_macos_carga_sin_avisos(tmp_path, caplog) -> None:
    from config import load_config
    path = tmp_path / "config.yaml"
    path.write_text(_wizard_config_yaml())
    with caplog.at_level("WARNING", logger="config"):
        cfg = load_config(path)
    assert cfg.display.backend == "ws"
    assert not caplog.records, caplog.text
