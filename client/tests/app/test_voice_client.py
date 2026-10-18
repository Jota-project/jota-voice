"""Tests focalizados de ciclo de vida en voice_client.

Cubre que los fallos de audio.start() queden visibles en el menubar y apaguen
limpiamente, que el hilo de asyncio no muera en silencio ni deje handlers de
señal inválidos, y que el run loop Cocoa se detenga tras el cleanup asíncrono.
"""
from __future__ import annotations

import asyncio
import os

import pytest
from unittest.mock import MagicMock, create_autospec

from app import voice_client
from backends import registry
from backends.audio_base import AudioBackend
from backends.display_null import NullDisplayBackend
from backends.gateway_client import GatewayClient, GatewayEvent
from backends.oww_base import OwWBackend
from ui.menubar_base import MenubarBackend

from config import Config, GatewayConfig

from tests.fakes.cocoa import requires_cocoa
from tests.fakes.gateway import make_ws, never
from tests.fakes.waiting import wait_until

# Los tests que invocan _run_with_cocoa_menubar hacen AppKit/Foundation
# en runtime (signal.set_wakeup_fd + NSFileHandle watcher, fix de #20).
# En Linux/Termux, AppKit no existe y el import lazy fallaría — solo se
# puede ejercitar este path en macOS con pyobjc: van marcados con
# @requires_cocoa (misma marca que el resto de tests Cocoa).


class _SpyMenubarBackend:
    def __init__(self) -> None:
        self.states: list[str] = []
        self.status_texts: list[str] = []
        self.commands = None
        self.run_forever_called = False

    def set_state(self, state: str) -> None:
        self.states.append(state)

    def set_status_text(self, text: str) -> None:
        self.status_texts.append(text)

    def set_listening_paused(self, paused: bool) -> None:
        pass

    def set_errors_count(self, n: int) -> None:
        pass

    def set_commands(self, cmds) -> None:
        self.commands = cmds

    def run_forever(self) -> None:
        self.run_forever_called = True


class _FailingAudioBackend:
    async def start(self) -> None:
        raise RuntimeError("permiso de micrófono denegado")

    async def stop(self) -> None:
        pass


def _make_cfg() -> Config:
    return Config(gateway=GatewayConfig(client_key="test", host="localhost"))


# Dobles con autospec de las interfaces reales (issue #38): una llamada a un
# método que la interfaz no tiene, o con otra firma, hace fallar el test.

def _audio_mock() -> MagicMock:
    return create_autospec(AudioBackend, instance=True)


def _oww_mock(run_forever=None) -> MagicMock:
    """``run_forever`` por defecto retorna al instante (oww_loop lo relanza
    tras _OWW_RESTART_DELAY_S); ``never`` lo deja colgado como el real."""
    oww = create_autospec(OwWBackend, instance=True)
    if run_forever is not None:
        oww.run_forever.side_effect = run_forever
    return oww


def _menubar_mock() -> MagicMock:
    """Menubar no bloqueante (MenubarBackend, sin run_forever/stop): el
    modo asyncio puro, como el NullMenubarBackend de Linux/Termux."""
    return create_autospec(MenubarBackend, instance=True)


def _gateway_mock(*, ws=None, connected: bool = False) -> MagicMock:
    """GatewayClient con autospec. ``_ws`` es atributo de instancia (autospec
    no lo ve) y main() lo consulta en el apagado: se fija explícitamente."""
    g = create_autospec(GatewayClient, instance=True)
    g._ws = ws
    g.is_connected = connected
    return g


async def _noop_on_wake(name: str) -> None:
    pass


def test_call_soon_threadsafe_safe_ignores_closed_loop() -> None:
    loop = asyncio.new_event_loop()
    stop_event = asyncio.Event()
    loop.close()

    voice_client._call_soon_threadsafe_safe(loop, stop_event)  # no debe lanzar


def test_call_soon_threadsafe_safe_sets_event_on_open_loop() -> None:
    loop = asyncio.new_event_loop()
    stop_event = asyncio.Event()

    voice_client._call_soon_threadsafe_safe(loop, stop_event)
    loop.run_until_complete(asyncio.sleep(0))
    loop.close()

    assert stop_event.is_set()


def _patch_gateway(monkeypatch) -> MagicMock:
    """Sustituye GatewayClient en main(): sin esto, con persistent_session
    (default) main() abría una conexión real a localhost."""
    gateway = _gateway_mock()
    monkeypatch.setattr(voice_client, "GatewayClient", lambda *a, **kw: gateway)
    return gateway


async def _test_audio_start_failure_is_visible_and_shuts_down_cleanly(monkeypatch) -> None:
    monkeypatch.setattr(registry, "make_audio", lambda cfg: _FailingAudioBackend())
    gateway = _patch_gateway(monkeypatch)
    mounted: list[str] = []

    class _Display(NullDisplayBackend):
        async def start(self) -> None:
            mounted.append("display.start")

    async def _sm(*a, **kw) -> None:
        mounted.append("sm_run")

    monkeypatch.setattr(registry, "make_display", lambda cfg, **kw: _Display())
    monkeypatch.setattr(voice_client, "sm_run", _sm)

    menubar = _SpyMenubarBackend()
    cfg = _make_cfg()

    # No debe propagar la excepción de audio.start() fuera de main().
    await voice_client.main(cfg, menubar, external_stop_event=asyncio.Event())

    assert "error" in menubar.states
    assert menubar.status_texts, "el usuario debe ver un texto descriptivo del fallo"
    # El pipeline no debe seguir montándose sobre un backend de audio muerto
    # (#112: antes solo se miraba commands, que se asigna al final).
    assert menubar.commands is None
    assert mounted == [], f"se montó parte del pipeline tras el fallo de audio: {mounted}"
    # La sesión eager no se queda abierta.
    gateway.disconnect.assert_awaited_once_with()


def test_audio_start_failure_is_visible_and_shuts_down_cleanly(monkeypatch) -> None:
    asyncio.run(_test_audio_start_failure_is_visible_and_shuts_down_cleanly(monkeypatch))


def test_main_passes_bus_to_oww_backend(monkeypatch) -> None:
    """Sin bus, OWWClient nunca publica oww_degraded/oww_recovered y el
    menubar no puede avisar de "Wake word no disponible"."""
    captured: dict = {}

    def _make_oww(cfg, **kwargs):
        captured.update(kwargs)
        return _oww_mock()

    monkeypatch.setattr(registry, "make_audio", lambda cfg: _FailingAudioBackend())
    monkeypatch.setattr(registry, "make_oww", _make_oww)
    _patch_gateway(monkeypatch)

    asyncio.run(
        voice_client.main(_make_cfg(), _SpyMenubarBackend(), external_stop_event=asyncio.Event())
    )

    assert isinstance(captured.get("bus"), voice_client.EventBus)


class _ExplodingMenubarBackend(_SpyMenubarBackend):
    def set_state(self, state: str) -> None:
        raise RuntimeError("menubar backend roto")


async def _test_audio_start_failure_does_not_propagate_if_menubar_reporting_fails(monkeypatch) -> None:
    monkeypatch.setattr(registry, "make_audio", lambda cfg: _FailingAudioBackend())
    _patch_gateway(monkeypatch)

    menubar = _ExplodingMenubarBackend()
    cfg = _make_cfg()

    # La excepción original de audio.start() no debe perderse su manejo
    # por un fallo secundario al reportarla al menubar.
    await voice_client.main(cfg, menubar, external_stop_event=asyncio.Event())


def test_audio_start_failure_does_not_propagate_if_menubar_reporting_fails(monkeypatch) -> None:
    asyncio.run(_test_audio_start_failure_does_not_propagate_if_menubar_reporting_fails(monkeypatch))


@requires_cocoa
def test_run_with_cocoa_menubar_survives_early_main_failure(monkeypatch) -> None:
    async def _fake_main(cfg, menubar_backend, *, external_stop_event=None):
        raise RuntimeError("fallo antes de construir nada")

    monkeypatch.setattr(voice_client, "main", _fake_main)

    signal_calls: list[tuple] = []
    monkeypatch.setattr(
        voice_client.signal, "signal",
        lambda sig, handler: signal_calls.append((sig, handler)),
    )

    menubar = _SpyMenubarBackend()
    cfg = _make_cfg()

    voice_client._run_with_cocoa_menubar(cfg, menubar)

    assert menubar.run_forever_called, "la app debe seguir mostrando el icono aunque main() falle"
    assert "error" in menubar.states


@requires_cocoa
def test_run_with_cocoa_menubar_installs_noop_signal_handlers(monkeypatch) -> None:
    """En Cocoa mode el NSFileHandle observer es quien procesa las señales,
    pero CPython solo escribe en el wakeup fd si la señal tiene un handler
    Python. Sin ellos (lo que exigía antes este test, #104) SIGTERM mataba
    el proceso en seco con exit 143, sin apagado ordenado. Deben instalarse
    handlers no-op para SIGTERM y SIGINT. La prueba con señales reales está
    en test_voice_client_cocoa_signals.py."""
    async def _fake_main(cfg, menubar_backend, *, external_stop_event=None):
        raise RuntimeError("fallo antes de construir nada")

    monkeypatch.setattr(voice_client, "main", _fake_main)

    signal_calls: list[tuple] = []
    monkeypatch.setattr(
        voice_client.signal, "signal",
        lambda sig, handler: signal_calls.append((sig, handler)),
    )

    menubar = _SpyMenubarBackend()
    cfg = _make_cfg()

    voice_client._run_with_cocoa_menubar(cfg, menubar)

    assert sorted(sig for sig, _ in signal_calls) == sorted(
        [voice_client.signal.SIGTERM, voice_client.signal.SIGINT]
    )
    for _sig, handler in signal_calls:
        assert callable(handler)
        handler(_sig, None)  # no-op: no debe lanzar


# ---------------------------------------------------------------------------
# Issue #20: SIGINT/SIGTERM no se procesaban hasta el siguiente tick del
# NSTimer (latencia hasta 1s con refresh_hz=1.0). El fix despierta el run
# loop de Cocoa vía signal.set_wakeup_fd() + NSFileHandle leyendo del pipe.
# ---------------------------------------------------------------------------


@requires_cocoa
def test_run_with_cocoa_menubar_sets_wakeup_fd(monkeypatch) -> None:
    """CPython solo invoca un handler de signal cuando el hilo principal
    ejecuta bytecode Python — en Cocoa mode el hilo está bloqueado en
    NSApp.run(). signal.set_wakeup_fd() hace que CPython escriba al pipe
    cuando llega una señal; el watcher de NSFileHandle en el run loop
    despierta NSApp inmediatamente. Sin esto, SIGTERM puede tardar
    hasta 1/refresh_hz en procesarse (peor caso: 1s con refresh_hz=1.0).

    El unit test verifica el wiring (set_wakeup_fd + pipe no bloqueante);
    el camino completo con señales reales (pipe → NSFileHandle → apagado)
    está en test_voice_client_cocoa_signals.py.
    """
    async def _fake_main(cfg, menubar_backend, *, external_stop_event=None):
        raise RuntimeError("fallo antes de construir nada")

    monkeypatch.setattr(voice_client, "main", _fake_main)
    monkeypatch.setattr(
        voice_client.signal, "signal",
        lambda sig, handler: None,
    )

    # Capturar set_wakeup_fd y el modo del fd en el momento de instalarlo
    # (luego el test no puede mirarlo: el pipe es interno).
    wakeup_calls: list[int] = []
    blocking_at_install: list[bool] = []

    def _set_wakeup_fd(fd: int) -> None:
        wakeup_calls.append(fd)
        blocking_at_install.append(os.get_blocking(fd))

    monkeypatch.setattr(voice_client.signal, "set_wakeup_fd", _set_wakeup_fd)

    # Stub de Foundation: NSNotificationCenter y NSFileHandle son objetos
    # PyObjC reales y no se pueden monkey-patchear en atributos individuales.
    # En su lugar, los sustituimos antes de importar voice_client.

    menubar = _SpyMenubarBackend()
    cfg = _make_cfg()

    voice_client._run_with_cocoa_menubar(cfg, menubar)

    # 1. set_wakeup_fd debe llamarse con un fd no-negativo (extremo de
    #    escritura del pipe que CPython usará para despertarse).
    assert len(wakeup_calls) == 1, (
        f"signal.set_wakeup_fd debe llamarse una vez (Cocoa mode); "
        f"se llamó {len(wakeup_calls)} veces: {wakeup_calls}"
    )
    assert wakeup_calls[0] is not None and wakeup_calls[0] >= 0, (
        f"set_wakeup_fd debe recibir el extremo de escritura del pipe; "
        f"got {wakeup_calls[0]}"
    )
    # 2. #112: CPython escribe en el wakeup fd desde el handler de C; si el
    #    fd fuese bloqueante, un pipe lleno colgaría el proceso.
    assert blocking_at_install == [False], "el wakeup fd debe ser O_NONBLOCK"


async def _test_menubar_stops_after_async_cleanup(monkeypatch) -> None:
    cleanup_order: list[str] = []

    class _AudioBackend:
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            cleanup_order.append("audio.stop")

    class _OwwBackend:
        async def run_forever(self, audio, on_wake) -> None:
            await asyncio.Future()

        async def disconnect(self) -> None:
            cleanup_order.append("oww.disconnect")

    class _GatewayClient:
        def __init__(self, cfg, **kwargs) -> None:
            pass

        async def connect(self) -> None:
            pass

        _ws = make_ws()
        _session_id = "test-session"

        async def receive(self):
            yield GatewayEvent(type="status", data={})

        async def disconnect(self) -> None:
            cleanup_order.append("gateway.disconnect")

    class _IdleClient:
        def __init__(self, *args) -> None:
            pass

        async def run(self, *args) -> None:
            await asyncio.Future()

    class _MenubarBackend(_SpyMenubarBackend):
        def stop(self) -> None:
            cleanup_order.append("menubar.stop")

    async def _completed_state_machine(*args, **kwargs) -> None:
        pass

    monkeypatch.setattr(registry, "make_audio", lambda cfg: _AudioBackend())
    monkeypatch.setattr(registry, "make_oww", lambda cfg, on_wake_word, **kw: _OwwBackend())

    class _Display(NullDisplayBackend):
        async def stop(self) -> None:
            cleanup_order.append("display.stop")

    monkeypatch.setattr(registry, "make_display", lambda cfg, **kw: _Display())
    monkeypatch.setattr(voice_client, "GatewayClient", _GatewayClient)
    monkeypatch.setattr(voice_client, "DisplayClient", _IdleClient)
    monkeypatch.setattr(voice_client, "MenubarClient", _IdleClient)
    monkeypatch.setattr(voice_client, "sm_run", _completed_state_machine)

    await voice_client.main(
        _make_cfg(), _MenubarBackend(), external_stop_event=asyncio.Event()
    )

    # El display va detrás de audio/gateway/OWW: su stop() puede tardar
    # hasta ~2 s con un display que no lee, y launchd mata a los 20 s.
    assert cleanup_order == [
        "audio.stop",
        "gateway.disconnect",
        "oww.disconnect",
        "display.stop",
        "menubar.stop",
    ]


def test_menubar_stops_after_async_cleanup(monkeypatch) -> None:
    asyncio.run(_test_menubar_stops_after_async_cleanup(monkeypatch))


@pytest.mark.asyncio
async def test_status_previos_a_ready_del_connect_inicial_llegan_a_display_y_menubar(monkeypatch) -> None:
    """Hueco de Fase 7: los `status` que el gateway manda antes de `ready`
    se publican durante el gateway.connect() inicial de main(), antes de que
    arranquen las tasks de MenubarClient y DisplayClient, y se perdían.
    Ahora ambos consumidores se suscriben antes del connect."""
    from app.display_client import DisplayClient
    from domain.event_bus import VoiceEvent

    display_sent: list[tuple[str, dict]] = []
    menubar_got: list[str] = []

    class _Display(NullDisplayBackend):
        def send(self, type: str, data: dict) -> None:
            display_sent.append((type, data))

    class _Gateway:
        def __init__(self, cfg, bus=None, **kw) -> None:
            self._bus = bus
            self._ws = None

        async def connect(self) -> None:
            # Como GatewayClient: status previo a ready -> bus.
            self._bus.publish(VoiceEvent(type="gateway_status",
                                         data={"service": "transcriber", "state": "unavailable"}))

        async def disconnect(self) -> None:
            pass

    class _Menubar:
        def __init__(self, *a, **kw) -> None:
            pass

        async def run(self, bus, ui_queue, pause_event, loop, stop_event, events=None):
            async for ev in (events if events is not None else bus.subscribe()):
                menubar_got.append(ev.type)

    sm_done = asyncio.Event()

    async def _sm(*a, **kw):
        await asyncio.sleep(0.05)  # deja drenar a los consumidores
        sm_done.set()

    audio = _audio_mock()
    monkeypatch.setattr(registry, "make_audio", lambda cfg: audio)
    monkeypatch.setattr(registry, "make_oww", lambda cfg, **kw: _oww_mock(run_forever=never))
    monkeypatch.setattr(registry, "make_display", lambda cfg, **kw: _Display())
    monkeypatch.setattr(voice_client, "GatewayClient", _Gateway)
    monkeypatch.setattr(voice_client, "DisplayClient", DisplayClient)
    monkeypatch.setattr(voice_client, "MenubarClient", _Menubar)
    monkeypatch.setattr(voice_client, "sm_run", _sm)

    cfg = _make_cfg()
    assert cfg.gateway.persistent_session
    await asyncio.wait_for(
        voice_client.main(cfg, _SpyMenubarBackend(), external_stop_event=asyncio.Event()),
        timeout=5.0,
    )
    assert sm_done.is_set()
    assert "gateway_status" in menubar_got
    healths = [d for t, d in display_sent if t == "health"]
    assert healths and healths[0]["key"] == "gateway.transcriber"
    assert healths[0]["level"] == "warning"


@pytest.mark.asyncio
async def test_main_arranca_y_para_el_display_y_su_cancel_llega_a_la_state_machine(monkeypatch) -> None:
    """Fase D: el `cancel` de un display (on_cancel del backend ws) activa el
    mismo cancel_event que recibe la state machine (antes: ControlServer)."""
    order: list[str] = []
    captured: dict = {}

    class _Display(NullDisplayBackend):
        async def start(self) -> None:
            order.append("display.start")

        async def stop(self) -> None:
            order.append("display.stop")

    def _make_display(cfg, *, on_cancel=None):
        captured["on_cancel"] = on_cancel
        return _Display()

    audio = _audio_mock()

    async def _sm(cfg, bus, audio, gateway, playback, cancel_event, **kw):
        order.append("sm")
        assert "display.start" in order
        assert not cancel_event.is_set()
        captured["on_cancel"]()
        assert cancel_event.is_set()

    class _IdleClient:
        def __init__(self, *a, **kw) -> None:
            pass

        async def run(self, *a, **kw) -> None:
            await asyncio.Future()

    monkeypatch.setattr(registry, "make_audio", lambda cfg: audio)
    monkeypatch.setattr(registry, "make_oww", lambda cfg, **kw: _oww_mock(run_forever=never))
    monkeypatch.setattr(registry, "make_display", _make_display)
    monkeypatch.setattr(voice_client, "GatewayClient", lambda *a, **kw: _gateway_mock())
    monkeypatch.setattr(voice_client, "DisplayClient", _IdleClient)
    monkeypatch.setattr(voice_client, "MenubarClient", _IdleClient)
    monkeypatch.setattr(voice_client, "sm_run", _sm)

    await asyncio.wait_for(
        voice_client.main(_make_cfg(), _SpyMenubarBackend(), external_stop_event=asyncio.Event()),
        timeout=5.0,
    )
    assert order == ["display.start", "sm", "display.stop"]


# ---------------------------------------------------------------------------
# Issue #24: gateway_idle_listener — consume receive() durante IDLE
# ---------------------------------------------------------------------------

def _make_gateway_mock(events_to_yield=None):
    """Mock de GatewayClient que emite una lista de eventos cuando se le pide receive()."""
    g = _gateway_mock(ws=make_ws(), connected=True)  # simula WS vivo
    if events_to_yield is None:
        events_to_yield = []

    async def _receive():
        for ev in events_to_yield:
            yield ev

    g.receive = _receive
    return g


def _make_bus():
    from domain.event_bus import EventBus
    return EventBus()


async def _collect(bus, timeout=1.0):
    """Suscribe al bus y recolecta eventos hasta el timeout."""
    captured = []
    async def _loop():
        async for ev in bus.subscribe():
            captured.append(ev)
            if ev.type in ("__stop__",):
                break
    try:
        await asyncio.wait_for(_loop(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    return captured


async def _test_idle_listener_publish_status_a_bus():
    """Issue #24: status proactivo durante IDLE aparece como gateway_status."""
    from app.voice_client import gateway_idle_listener
    bus = _make_bus()
    gateway = _make_gateway_mock([
        GatewayEvent(type="status", data={"service": "tts", "state": "unavailable"}),
        # terminamos con un type desconocido para forzar salida
    ])

    listener_task = asyncio.create_task(gateway_idle_listener(bus, gateway))

    # Esperar un momento para que reciba el evento
    captured = await _collect(bus, timeout=0.5)
    listener_task.cancel()
    try:
        await listener_task
    except asyncio.CancelledError:
        pass

    status_evts = [e for e in captured if e.type == "gateway_status"]
    assert len(status_evts) >= 1
    assert status_evts[0].data["service"] == "tts"


def test_idle_listener_publish_status_a_bus():
    asyncio.run(_test_idle_listener_publish_status_a_bus())


async def _test_idle_listener_descarta_push_turn_solo_logea():
    """Issue #24: turn_start durante IDLE se loguea y descarta, no publica state_changed."""
    from app.voice_client import gateway_idle_listener
    bus = _make_bus()
    gateway = _gateway_mock(ws=make_ws(), connected=True)

    async def _receive_turns():
        """Generator que emite varios turn_start y luego se agota."""
        for i in range(5):
            yield GatewayEvent(type="turn_start", data={"turn_id": f"p-{i}", "turn_seq": i})

    gateway.receive = _receive_turns

    listener_task = asyncio.create_task(gateway_idle_listener(bus, gateway))
    captured = await _collect(bus, timeout=0.5)

    state_changed = [e for e in captured if e.type == "state_changed"]
    # El assertion clave: turn_start NO se traduce en state_changed
    assert not state_changed, "no debe publicar state_changed ante un push turn"


def test_idle_listener_descarta_push_turn_solo_logea():
    asyncio.run(_test_idle_listener_descarta_push_turn_solo_logea())


async def _test_idle_listener_exception_no_controlada_publica_error():
    """Issue #24: excepción inesperada en el listener se reporta y termina limpiamente."""
    from app.voice_client import gateway_idle_listener
    bus = _make_bus()
    gateway = _gateway_mock(ws=make_ws(), connected=True)

    async def _boom():
        raise RuntimeError("test explosion")
        yield  # nunca llega, pero el generador debe ser async

    gateway.receive = _boom

    listener_task = asyncio.create_task(gateway_idle_listener(bus, gateway))
    # Recolectar DURING execution (antes de que el task termine)
    captured = await _collect(bus, timeout=0.5)
    # Esperar a que termine (el boom explota en el primer __anext__)
    try:
        await asyncio.wait_for(listener_task, timeout=0.5)
    except (asyncio.TimeoutError, RuntimeError):
        pass
    listener_task.cancel()

    error_evts = [e for e in captured if e.type == "error"]
    assert len(error_evts) >= 1, "debe publicar VoiceEvent(error) ante excepción"
    assert "test explosion" in str(error_evts[0].data.get("message", ""))


def test_idle_listener_exception_no_controlada_publica_error():
    asyncio.run(_test_idle_listener_exception_no_controlada_publica_error())


# ---------------------------------------------------------------------------
# Issue #24: voice_client.main() — eager connect, idle_task lifecycle, shutdown
# ---------------------------------------------------------------------------


async def _test_main_llama_connect_una_sola_vez_y_arranca_idle_listener(monkeypatch):
    """Issue #24: main() conecta eager al gateway si persistent_session=True,
    e instancia gateway_idle_listener como task de fondo."""
    from config import Config, GatewayConfig, AudioConfig, DeviceConfig

    cfg = Config(
        gateway=GatewayConfig(
            host="127.0.0.1",
            client_key="x",
            persistent_session=True,
            connect_timeout_s=1.0,
        ),
        device=DeviceConfig(id="test"),
        audio=AudioConfig(),
    )

    gateway_mock = _gateway_mock(ws=make_ws(), connected=True)

    async def _receive_forever():
        # Necesary for idle_listener: it calls gen.__anext__() and checks ev.type
        yield GatewayEvent(type="status", data={})

    gateway_mock.receive = _receive_forever

    audio_mock = _audio_mock()

    sm_started = asyncio.Event()

    sm_kwargs: dict = {}

    async def _fake_sm_run(cfg, bus, audio, gateway, playback, cancel_event=None, **kw):
        sm_kwargs.update(kw)
        sm_started.set()
        await asyncio.Event().wait()

    class _IdleClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def run(self, *args, **kwargs) -> None:
            await asyncio.Future()

    monkeypatch.setattr(registry, "make_audio", lambda cfg: audio_mock)
    monkeypatch.setattr(registry, "make_oww", lambda cfg, **kw: _oww_mock())
    monkeypatch.setattr(registry, "make_display", lambda cfg, **kw: NullDisplayBackend())
    monkeypatch.setattr(voice_client, "sm_run", _fake_sm_run)
    monkeypatch.setattr(voice_client, "GatewayClient", lambda *a, **kw: gateway_mock)
    monkeypatch.setattr(voice_client, "DisplayClient", _IdleClient)
    monkeypatch.setattr(voice_client, "MenubarClient", _IdleClient)

    stop_ext = asyncio.Event()

    main_task = asyncio.create_task(
        voice_client.main(cfg, _menubar_mock(), external_stop_event=stop_ext)
    )
    await sm_started.wait()
    stop_ext.set()

    await asyncio.wait_for(main_task, timeout=3.0)

    gateway_mock.connect.assert_awaited_once()
    # #118: main() entrega a la state machine el supervisor de IDLE (que
    # sustituye al idle_listener + coordinator).
    assert sm_kwargs.get("idle_background") is not None


def test_main_llama_connect_una_sola_vez_y_arranca_idle_listener(monkeypatch):
    asyncio.run(_test_main_llama_connect_una_sola_vez_y_arranca_idle_listener(monkeypatch))


async def _test_main_shutdown_desconecta_una_vez(monkeypatch):
    """Issue #24: main() llama gateway.disconnect() una sola vez en shutdown."""
    from config import Config, GatewayConfig, AudioConfig, DeviceConfig

    cfg = Config(
        gateway=GatewayConfig(
            host="127.0.0.1",
            client_key="x",
            persistent_session=True,
            connect_timeout_s=1.0,
        ),
        device=DeviceConfig(id="test"),
        audio=AudioConfig(),
    )

    gateway_mock = _gateway_mock(ws=make_ws(), connected=True)

    async def _receive_forever():
        # Necesary for idle_listener: it calls gen.__anext__() and checks ev.type
        yield GatewayEvent(type="status", data={})

    gateway_mock.receive = _receive_forever

    audio_mock = _audio_mock()

    async def _fake_sm_run(cfg, bus, audio, gateway, playback, cancel_event=None, **kw):
        await asyncio.Event().wait()

    class _IdleClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def run(self, *args, **kwargs) -> None:
            await asyncio.Future()

    monkeypatch.setattr(registry, "make_audio", lambda cfg: audio_mock)
    monkeypatch.setattr(registry, "make_oww", lambda cfg, **kw: _oww_mock())
    monkeypatch.setattr(registry, "make_display", lambda cfg, **kw: NullDisplayBackend())
    monkeypatch.setattr(voice_client, "sm_run", _fake_sm_run)
    monkeypatch.setattr(voice_client, "GatewayClient", lambda *a, **kw: gateway_mock)
    monkeypatch.setattr(voice_client, "DisplayClient", _IdleClient)
    monkeypatch.setattr(voice_client, "MenubarClient", _IdleClient)

    stop_ext = asyncio.Event()

    main_task = asyncio.create_task(
        voice_client.main(cfg, _menubar_mock(), external_stop_event=stop_ext)
    )
    await asyncio.sleep(0.1)
    stop_ext.set()

    await asyncio.wait_for(main_task, timeout=3.0)

    gateway_mock.disconnect.assert_awaited_once()


def test_main_shutdown_desconecta_una_vez(monkeypatch):
    asyncio.run(_test_main_shutdown_desconecta_una_vez(monkeypatch))


# ---------------------------------------------------------------------------
# Los dos tests del _idle_coordinator (test_idle_coordinator_cancels_idle_task_
# on_playback_started y test_idle_coordinator_tts_chunk_no_llega_al_idle_
# listener) se han eliminado junto con el coordinator (Fase R, #118):
#   - Probaban una COPIA del coordinator escrita dentro del test, no el de
#     main(). El primero fallaba porque el mock de receive() se agotaba tras un
#     evento: el listener ya había terminado solo y la copia solo ponía el
#     holder a None si la tarea seguía viva. El segundo no tenía coordinator y
#     esperaba un asyncio.Event que nadie ponía: se colgaba siempre.
#   - El coordinator real además tenía bugs propios: se re-armaba con
#     cualquier `error` (también a mitad de turno → dos lectores del socket) y
#     moría al terminar un push en IDLE (nadie volvía a leer).
# Sustituidos por tests/domain/test_gateway_session.py
# (test_idle_cancela_el_supervisor_antes_de_volver,
#  test_un_solo_lector_del_socket_durante_un_turno_completo).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Issue #30: _shutdown_service() no debe bloquear el hilo de Cocoa.
# Verificamos que la callback mande "shutdown_service" a ui_queue en vez
# de ejecutar subprocess.run() directamente.
# ---------------------------------------------------------------------------


def test_shutdown_service_puts_sentinel_in_ui_queue(monkeypatch) -> None:
    """Issue #30: invocar la callback on_shutdown_service debe depositar
    'shutdown_service' en ui_queue (no ejecutar subprocess.run sync).

    Antes el test replicaba la callback dentro del propio test y no tocaba
    voice_client (#112). Ahora usa los MenubarCommands reales que main()
    entrega al menubar y la ui_queue real que pasa a MenubarClient."""
    got: dict = {}

    class _MenubarClient:
        def __init__(self, *a, **kw) -> None:
            pass

        async def run(self, bus, ui_queue, *a, **kw) -> None:
            got["ui_queue"] = ui_queue
            await asyncio.Future()

    class _IdleClient:
        def __init__(self, *a, **kw) -> None:
            pass

        async def run(self, *a, **kw) -> None:
            await asyncio.Future()

    menubar = _SpyMenubarBackend()

    async def _sm(*a, **kw) -> None:
        # Deja arrancar a MenubarClient antes de pulsar el botón.
        await wait_until(lambda: "ui_queue" in got)
        menubar.commands.on_shutdown_service()

    def _no_sync_subprocess(*a, **kw):
        raise AssertionError("on_shutdown_service no debe ejecutar subprocess.run en el hilo de UI")

    monkeypatch.setattr(voice_client.subprocess, "run", _no_sync_subprocess)
    monkeypatch.setattr(registry, "make_audio", lambda cfg: _audio_mock())
    monkeypatch.setattr(registry, "make_oww", lambda cfg, **kw: _oww_mock(run_forever=never))
    monkeypatch.setattr(registry, "make_display", lambda cfg, **kw: NullDisplayBackend())
    _patch_gateway(monkeypatch)
    monkeypatch.setattr(voice_client, "MenubarClient", _MenubarClient)
    monkeypatch.setattr(voice_client, "DisplayClient", _IdleClient)
    monkeypatch.setattr(voice_client, "sm_run", _sm)

    asyncio.run(asyncio.wait_for(
        voice_client.main(_make_cfg(), menubar, external_stop_event=asyncio.Event()),
        timeout=5.0,
    ))

    assert got["ui_queue"].get_nowait() == "shutdown_service"
    assert got["ui_queue"].empty()


# Issue #104 (el observer de NSFileHandle loguea "señal recibida"): el test
# que había aquí escribía él mismo esa línea en el log y comprobaba que
# estaba (#112: nunca tocaba voice_client). La comprobación vive ahora en
# test_voice_client_cocoa_signals.py, contra el handler real y con una
# señal real.


# ---------------------------------------------------------------------------
# Cuelgue intermitente de test_main_*: _oww_loop se tragaba la cancelación
# exterior (patrón `cancel(); try: await t except CancelledError: pass`) y,
# con un run_forever que retorna al instante, giraba para siempre durante el
# apagado. Ahora es una función de módulo testeable.
# ---------------------------------------------------------------------------


def test_oww_loop_termina_al_cancelar_aunque_run_forever_retorne_al_instante() -> None:
    async def _t() -> None:
        oww = _oww_mock()
        task = asyncio.create_task(
            voice_client.oww_loop(oww, _audio_mock(), _noop_on_wake, asyncio.Event())
        )
        await asyncio.sleep(0.1)
        for _ in range(20):  # cancelar en distintos puntos del bucle
            task.cancel()
            done, _ = await asyncio.wait([task], timeout=0.05)
            if done:
                break
        assert task.done() and task.cancelled(), "oww_loop no respeta la cancelación"

    asyncio.run(_t())


def test_oww_loop_no_gira_en_caliente_si_run_forever_retorna() -> None:
    async def _t() -> None:
        oww = _oww_mock()
        task = asyncio.create_task(
            voice_client.oww_loop(oww, _audio_mock(), _noop_on_wake, asyncio.Event())
        )
        await asyncio.sleep(0.3)
        task.cancel()
        await asyncio.wait([task], timeout=1.0)
        assert oww.run_forever.await_count <= 5, (
            f"run_forever relanzado {oww.run_forever.await_count} veces en 0.3s"
        )

    asyncio.run(_t())



def test_main_no_aborta_si_la_conexion_inicial_falla(monkeypatch) -> None:
    """#118: con la red aún no lista al arrancar, main() antes abortaba (y
    launchd lo relanzaba en bucle). Ahora sigue y reintenta en IDLE."""
    from config import AudioConfig, DeviceConfig

    async def _t() -> None:
        cfg = Config(
            gateway=GatewayConfig(host="127.0.0.1", client_key="x", connect_timeout_s=1.0),
            device=DeviceConfig(id="test"),
            audio=AudioConfig(),
        )
        gateway_mock = _gateway_mock()
        gateway_mock.connect.side_effect = OSError("network unreachable")
        audio_mock = _audio_mock()
        sm_started = asyncio.Event()

        async def _fake_sm_run(*a, **kw):
            sm_started.set()
            await asyncio.Event().wait()

        class _IdleClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def run(self, *args, **kwargs) -> None:
                await asyncio.Future()

        monkeypatch.setattr(registry, "make_audio", lambda cfg: audio_mock)
        monkeypatch.setattr(registry, "make_oww", lambda cfg, **kw: _oww_mock())
        monkeypatch.setattr(registry, "make_display", lambda cfg, **kw: NullDisplayBackend())
        monkeypatch.setattr(voice_client, "sm_run", _fake_sm_run)
        monkeypatch.setattr(voice_client, "GatewayClient", lambda *a, **kw: gateway_mock)
        monkeypatch.setattr(voice_client, "DisplayClient", _IdleClient)
        monkeypatch.setattr(voice_client, "MenubarClient", _IdleClient)

        stop_ext = asyncio.Event()
        main_task = asyncio.create_task(
            voice_client.main(cfg, _menubar_mock(), external_stop_event=stop_ext)
        )
        await asyncio.wait_for(sm_started.wait(), 2.0)
        stop_ext.set()
        await asyncio.wait_for(main_task, timeout=3.0)

    asyncio.run(_t())


def test_main_pasa_el_bus_al_gateway_client(monkeypatch) -> None:
    """#110: sin bus, gateway_ready (session_id/capabilities) no llegaba nunca
    al bus en producción."""
    captured = {}

    class _Boom(Exception):
        pass

    def _fake_gateway(*a, **kw):
        captured.update(kw)
        raise _Boom()

    monkeypatch.setattr(registry, "make_audio", lambda cfg: _audio_mock())
    monkeypatch.setattr(registry, "make_oww", lambda cfg, **kw: _oww_mock())
    monkeypatch.setattr(registry, "make_display", lambda cfg, **kw: NullDisplayBackend())
    monkeypatch.setattr(voice_client, "GatewayClient", _fake_gateway)
    with pytest.raises(_Boom):
        asyncio.run(voice_client.main(_make_cfg(), _menubar_mock()))
    assert captured.get("bus") is not None
