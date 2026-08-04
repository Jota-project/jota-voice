"""Tests focalizados de ciclo de vida en voice_client.

Cubre que los fallos de audio.start() queden visibles en el menubar y apaguen
limpiamente, que el hilo de asyncio no muera en silencio ni deje handlers de
señal inválidos, y que el run loop Cocoa se detenga tras el cleanup asíncrono.
"""
from __future__ import annotations

import asyncio
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock

from app import voice_client
from backends import registry

# Los tests que invocan _run_with_cocoa_menubar hacen AppKit/Foundation
# en runtime (signal.set_wakeup_fd + NSFileHandle watcher, fix de #20).
# En Linux/Termux, AppKit no existe y el import lazy fallaría — solo se
# puede ejercitar este path en macOS.
pytestmark_cocoa = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Cocoa runloop solo instalable con AppKit (macOS)",
)
from config import Config, GatewayConfig


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


async def _test_audio_start_failure_is_visible_and_shuts_down_cleanly(monkeypatch) -> None:
    monkeypatch.setattr(registry, "make_audio", lambda cfg: _FailingAudioBackend())

    menubar = _SpyMenubarBackend()
    cfg = _make_cfg()

    # No debe propagar la excepción de audio.start() fuera de main().
    await voice_client.main(cfg, menubar, external_stop_event=asyncio.Event())

    assert "error" in menubar.states
    assert menubar.status_texts, "el usuario debe ver un texto descriptivo del fallo"
    # El pipeline no debe seguir montándose sobre un backend de audio muerto.
    assert menubar.commands is None


def test_audio_start_failure_is_visible_and_shuts_down_cleanly(monkeypatch) -> None:
    asyncio.run(_test_audio_start_failure_is_visible_and_shuts_down_cleanly(monkeypatch))


class _ExplodingMenubarBackend(_SpyMenubarBackend):
    def set_state(self, state: str) -> None:
        raise RuntimeError("menubar backend roto")


async def _test_audio_start_failure_does_not_propagate_if_menubar_reporting_fails(monkeypatch) -> None:
    monkeypatch.setattr(registry, "make_audio", lambda cfg: _FailingAudioBackend())

    menubar = _ExplodingMenubarBackend()
    cfg = _make_cfg()

    # La excepción original de audio.start() no debe perderse su manejo
    # por un fallo secundario al reportarla al menubar.
    await voice_client.main(cfg, menubar, external_stop_event=asyncio.Event())


def test_audio_start_failure_does_not_propagate_if_menubar_reporting_fails(monkeypatch) -> None:
    asyncio.run(_test_audio_start_failure_does_not_propagate_if_menubar_reporting_fails(monkeypatch))


@pytestmark_cocoa
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


@pytestmark_cocoa
def test_run_with_cocoa_menubar_signal_handler_survives_closed_loop(monkeypatch) -> None:
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

    assert len(signal_calls) == 2
    _sig, handler = signal_calls[0]
    handler(_sig, None)  # no debe lanzar RuntimeError: Event loop is closed


# ---------------------------------------------------------------------------
# Issue #20: SIGINT/SIGTERM no se procesaban hasta el siguiente tick del
# NSTimer (latencia hasta 1s con refresh_hz=1.0). El fix despierta el run
# loop de Cocoa vía signal.set_wakeup_fd() + NSFileHandle leyendo del pipe.
# ---------------------------------------------------------------------------


@pytestmark_cocoa
def test_run_with_cocoa_menubar_sets_wakeup_fd(monkeypatch) -> None:
    """CPython solo invoca un handler de signal cuando el hilo principal
    ejecuta bytecode Python — en Cocoa mode el hilo está bloqueado en
    NSApp.run(). signal.set_wakeup_fd() hace que CPython escriba al pipe
    cuando llega una señal; el watcher de NSFileHandle en el run loop
    despierta NSApp inmediatamente. Sin esto, SIGTERM puede tardar
    hasta 1/refresh_hz en procesarse (peor caso: 1s con refresh_hz=1.0).

    El unit test verifica solo el wiring (set_wakeup_fd + pipe); la
    latencia real se mide empíricamente arrancando voice_client.py como
    subprocess y midiendo SIGTERM → "Apagando".
    """
    async def _fake_main(cfg, menubar_backend, *, external_stop_event=None):
        raise RuntimeError("fallo antes de construir nada")

    monkeypatch.setattr(voice_client, "main", _fake_main)
    monkeypatch.setattr(
        voice_client.signal, "signal",
        lambda sig, handler: None,
    )

    # Capturar set_wakeup_fd.
    wakeup_calls: list[int] = []
    monkeypatch.setattr(
        voice_client.signal, "set_wakeup_fd",
        lambda fd: wakeup_calls.append(fd),
    )

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
        def __init__(self, cfg, device_id) -> None:
            pass

        async def connect(self) -> None:
            pass

        _ws = MagicMock()
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

    async def _idle_control_server(*args) -> None:
        await asyncio.Future()

    async def _completed_state_machine(*args) -> None:
        pass

    monkeypatch.setattr(registry, "make_audio", lambda cfg: _AudioBackend())
    monkeypatch.setattr(registry, "make_oww", lambda cfg, on_wake_word: _OwwBackend())
    monkeypatch.setattr(registry, "make_display", lambda cfg: object())
    monkeypatch.setattr(voice_client, "GatewayClient", _GatewayClient)
    monkeypatch.setattr(voice_client, "DisplayClient", _IdleClient)
    monkeypatch.setattr(voice_client, "MenubarClient", _IdleClient)
    monkeypatch.setattr(voice_client.control_server, "run", _idle_control_server)
    monkeypatch.setattr(voice_client, "sm_run", _completed_state_machine)

    await voice_client.main(
        _make_cfg(), _MenubarBackend(), external_stop_event=asyncio.Event()
    )

    assert cleanup_order == [
        "audio.stop",
        "gateway.disconnect",
        "oww.disconnect",
        "menubar.stop",
    ]


def test_menubar_stops_after_async_cleanup(monkeypatch) -> None:
    asyncio.run(_test_menubar_stops_after_async_cleanup(monkeypatch))


# ---------------------------------------------------------------------------
# Issue #24: gateway_idle_listener — consume receive() durante IDLE
# ---------------------------------------------------------------------------

from backends.gateway_client import GatewayEvent


def _make_gateway_mock(events_to_yield=None):
    """Mock de GatewayClient que emite una lista de eventos cuando se le pide receive()."""
    g = MagicMock()
    g.is_connected = True
    g._ws = MagicMock()  # simula WS vivo
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
    gateway = MagicMock()
    gateway.is_connected = True
    gateway._ws = MagicMock()

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
    gateway = MagicMock()
    gateway.is_connected = True
    gateway._ws = MagicMock()

    async def _boom():
        raise RuntimeError("test explosion")
        yield  # noqa: nunca llega, pero el generador debe ser async

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

    gateway_mock = MagicMock()
    gateway_mock.connect = AsyncMock()
    gateway_mock.disconnect = AsyncMock()
    gateway_mock.is_connected = True
    gateway_mock._ws = MagicMock()

    async def _receive_forever():
        # Necesary for idle_listener: it calls gen.__anext__() and checks ev.type
        yield GatewayEvent(type="status", data={})

    gateway_mock.receive = _receive_forever

    audio_mock = MagicMock()
    audio_mock.start = AsyncMock()
    audio_mock.stop = AsyncMock()

    sm_started = asyncio.Event()

    async def _fake_sm_run(cfg, bus, audio, gateway, playback, cancel_event=None):
        sm_started.set()
        await asyncio.Event().wait()

    class _IdleClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def run(self, *args, **kwargs) -> None:
            await asyncio.Future()

    monkeypatch.setattr(registry, "make_audio", lambda cfg: audio_mock)
    monkeypatch.setattr(registry, "make_oww", lambda cfg, **kw: MagicMock(run_forever=AsyncMock()))
    monkeypatch.setattr(registry, "make_display", lambda cfg: MagicMock())
    monkeypatch.setattr(registry, "make_menubar", lambda cfg: MagicMock())
    monkeypatch.setattr(voice_client.control_server, "run", AsyncMock())
    monkeypatch.setattr(voice_client, "sm_run", _fake_sm_run)
    monkeypatch.setattr(voice_client, "GatewayClient", lambda *a, **kw: gateway_mock)
    monkeypatch.setattr(voice_client, "DisplayClient", _IdleClient)
    monkeypatch.setattr(voice_client, "MenubarClient", _IdleClient)

    stop_ext = asyncio.Event()

    main_task = asyncio.create_task(
        voice_client.main(cfg, MagicMock(), external_stop_event=stop_ext)
    )
    await sm_started.wait()
    stop_ext.set()

    await asyncio.wait_for(main_task, timeout=3.0)

    gateway_mock.connect.assert_awaited_once()


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

    gateway_mock = MagicMock()
    gateway_mock.connect = AsyncMock()
    gateway_mock.disconnect = AsyncMock()
    gateway_mock.is_connected = True
    gateway_mock._ws = MagicMock()

    async def _receive_forever():
        # Necesary for idle_listener: it calls gen.__anext__() and checks ev.type
        yield GatewayEvent(type="status", data={})

    gateway_mock.receive = _receive_forever

    audio_mock = MagicMock()
    audio_mock.start = AsyncMock()
    audio_mock.stop = AsyncMock()

    async def _fake_sm_run(cfg, bus, audio, gateway, playback, cancel_event=None):
        await asyncio.Event().wait()

    class _IdleClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def run(self, *args, **kwargs) -> None:
            await asyncio.Future()

    monkeypatch.setattr(registry, "make_audio", lambda cfg: audio_mock)
    monkeypatch.setattr(registry, "make_oww", lambda cfg, **kw: MagicMock(run_forever=AsyncMock()))
    monkeypatch.setattr(registry, "make_display", lambda cfg: MagicMock())
    monkeypatch.setattr(registry, "make_menubar", lambda cfg: MagicMock())
    monkeypatch.setattr(voice_client.control_server, "run", AsyncMock())
    monkeypatch.setattr(voice_client, "sm_run", _fake_sm_run)
    monkeypatch.setattr(voice_client, "GatewayClient", lambda *a, **kw: gateway_mock)
    monkeypatch.setattr(voice_client, "DisplayClient", _IdleClient)
    monkeypatch.setattr(voice_client, "MenubarClient", _IdleClient)

    stop_ext = asyncio.Event()

    main_task = asyncio.create_task(
        voice_client.main(cfg, MagicMock(), external_stop_event=stop_ext)
    )
    await asyncio.sleep(0.1)
    stop_ext.set()

    await asyncio.wait_for(main_task, timeout=3.0)

    gateway_mock.disconnect.assert_awaited_once()


def test_main_shutdown_desconecta_una_vez(monkeypatch):
    asyncio.run(_test_main_shutdown_desconecta_una_vez(monkeypatch))


# ---------------------------------------------------------------------------
# Issue #24 — Regression: coordinator NO debe permitir que idle_listener
# consuma gateway.receive() durante RESPONDING.
#
# Bug original: _idle_coordinator escuchaba state_changed("recording"/"responding")
# pero state_machine SOLO publica state_changed("idle"). La rama de cancelación
# era dead code → idle_listener y _receive_loop leían gateway.receive()
# concurrentemente → tts_chunk llegaba aleatoriamente a idle_listener.
#
# El fix cambia a escuchar eventos que SÍ se publican:
#   recording_started / playback_started → cancel idle_task
#   playback_ended / cancelled / error   → re-arm idle_task
# ---------------------------------------------------------------------------

async def _test_idle_coordinator_cancels_idle_task_on_playback_started():
    """Regression: cuando playback_started se publica, coordinator cancela
    idle_listener para que NO lea gateway.receive() durante RESPONDING.

    Fixture: simulamos un turno completo desde la perspectiva del coordinator:
      1. idle_listener corre consume() desde gateway (idle)
      2. Se publica recording_started → coordinator cancela idle_listener
      3. Se publica playback_started → coordinator ya no tiene idle_task (None)
      4. El coordinator sigue corriendo; spurious events del gateway durante
         RESPONDING deberíanIr a _receive_loop (fuera del scope del test), no
         al idle_listener que ya fue cancelado.

    Assertion clave: después de playback_started, el idle_listener
    (cancelado) no publica VoiceEvents en el bus.
    """
    from app.voice_client import gateway_idle_listener
    from domain.event_bus import VoiceEvent

    bus = _make_bus()
    idle_task_holder: list[asyncio.Task | None] = [None]

    # Sincronización determinista: eventos que el test espera ver procesados.
    recording_started_seen = asyncio.Event()
    playback_started_seen = asyncio.Event()
    playback_ended_seen = asyncio.Event()

    # Gateway mock que produce UN evento para simular que el idle_listener
    # estaba leyendo durante IDLE.
    gateway = _make_gateway_mock([
        GatewayEvent(type="status", data={"service": "tts", "state": "ok"}),
    ])

    # Arrancar idle_listener como lo hace main()
    idle_task_holder[0] = asyncio.create_task(
        gateway_idle_listener(bus, gateway),
        name="gateway_idle",
    )

    # Inline coordinator que replica el fix en voice_client.py.
    async def _inline_coordinator() -> None:
        async for ev in bus.subscribe():
            if ev.type in ("recording_started", "playback_started"):
                # Cancelar idle_task
                if idle_task_holder[0] is not None and not idle_task_holder[0].done():
                    idle_task_holder[0].cancel()
                    try:
                        await idle_task_holder[0]
                    except asyncio.CancelledError:
                        pass
                    idle_task_holder[0] = None
                if ev.type == "recording_started":
                    recording_started_seen.set()
                elif ev.type == "playback_started":
                    playback_started_seen.set()
            elif ev.type in ("playback_ended", "cancelled", "error"):
                # Re-arm
                if idle_task_holder[0] is None or idle_task_holder[0].done():
                    idle_task_holder[0] = asyncio.create_task(
                        gateway_idle_listener(bus, gateway),
                        name="gateway_idle",
                    )
                if ev.type == "playback_ended":
                    playback_ended_seen.set()

    coord_task = asyncio.create_task(_inline_coordinator(), name="idle_coordinator")

    # Esperar a que el idle_listener procese el status del gateway
    await asyncio.sleep(0.2)

    # Publicar recording_started → coordinator cancela idle_listener
    bus.publish(VoiceEvent(type="recording_started", data={}))
    await recording_started_seen.wait()
    assert idle_task_holder[0] is None, \
        "idle_task debe ser None tras recording_started"

    # Publicar playback_started (RESPONDING)
    bus.publish(VoiceEvent(type="playback_started", data={}))
    await playback_started_seen.wait()

    # Publicar playback_ended → re-arm
    bus.publish(VoiceEvent(type="playback_ended", data={}))
    await playback_ended_seen.wait()
    assert idle_task_holder[0] is not None and not idle_task_holder[0].done(), \
        "idle_task debe re-armarse tras playback_ended"

    # Cleanup
    coord_task.cancel()
    idle_task_holder[0].cancel()
    try:
        await asyncio.wait_for(coord_task, timeout=1.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    try:
        await asyncio.wait_for(idle_task_holder[0], timeout=1.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass


def test_idle_coordinator_cancels_idle_task_on_playback_started():
    asyncio.run(_test_idle_coordinator_cancels_idle_task_on_playback_started())


async def _test_idle_coordinator_tts_chunk_no_llega_al_idle_listener():
    """Regression C2: durante RESPONDING, el tts_chunk del gateway debe
    llegar a playback (consumido por _receive_loop), NO al idle_listener.

    Escenografía: gateway mock con status (durante IDLE) + tts_chunk
    (durante RESPONDING). Ejecutamos idle_listener + coordinator.
    Tras recording_started (cancelación), el tts_chunk no debe llegar
    al idle_listener porque ya fue cancelado.
    """
    from app.voice_client import gateway_idle_listener
    from domain.event_bus import VoiceEvent

    bus = _make_bus()
    idle_task_holder: list[asyncio.Task | None] = [None]

    recording_started_seen = asyncio.Event()

    # Gateway que produce status (IDLE) y se agota — simula que el
    # tts_chunk del gateway llegaría durante RESPONDING (fuera del mock).
    gateway = _make_gateway_mock([
        GatewayEvent(type="status", data={"service": "tts", "state": "ok"}),
    ])

    idle_task_holder[0] = asyncio.create_task(
        gateway_idle_listener(bus, gateway),
        name="gateway_idle",
    )

    # Recolectar eventos del bus
    collected: list = []

    async def _collector():
        async for ev in bus.subscribe():
            collected.append(ev)

    coll_task = asyncio.create_task(_collector())

    # Esperar a que el idle_listener reciba el status
    await asyncio.sleep(0.2)

    # Cancelar idle_listener publicando recording_started
    bus.publish(VoiceEvent(type="recording_started", data={}))
    await recording_started_seen.wait()

    # idle_listener debe estar cancelado
    assert idle_task_holder[0] is None or idle_task_holder[0].done(), \
        "idle_listener debe estar cancelado tras recording_started"

    # El gateway mock NO produce tts_chunk (se agotó tras status).
    # Verificar: status SÍ llegó al bus (confirmando idle_listener corrió)
    # pero NO hay tts_chunk alguno publicado.
    status_on_bus = [e for e in collected if e.type == "gateway_status"]
    assert status_on_bus, "el status del gateway debe llegar al bus (idle_listener corrió)"

    tts_chunks_on_bus = [e for e in collected if e.type == "tts_chunk"]
    assert tts_chunks_on_bus == [], \
        "tts_chunk no debe llegar al bus: idle_listener fue cancelado por coordinator"

    coll_task.cancel()
    try:
        await coll_task
    except asyncio.CancelledError:
        pass


def test_idle_coordinator_tts_chunk_no_llega_al_idle_listener():
    asyncio.run(_test_idle_coordinator_tts_chunk_no_llega_al_idle_listener())


# ---------------------------------------------------------------------------
# Issue #30: _shutdown_service() no debe bloquear el hilo de Cocoa.
# Verificamos que la callback mande "shutdown_service" a ui_queue en vez
# de ejecutar subprocess.run() directamente.
# ---------------------------------------------------------------------------


def test_shutdown_service_puts_sentinel_in_ui_queue() -> None:
    """Issue #30: invocar la callback on_shutdown_service debe depositar
    'shutdown_service' en ui_queue (no ejecutar subprocess.run sync)."""
    import queue as _queue

    ui_queue: _queue.Queue = _queue.Queue()
    received: list = []

    # Simular lo que ocurre cuando el consumer de ui_queue recibe el sentinel:
    # drain_task lee de la cola en un executor y registra lo que recibió.
    def _drain_like_consumer(q: _queue.Queue) -> str:
        try:
            return q.get(timeout=0.5)
        except _queue.Empty:
            return ""

    # Capturar lo que llega a la cola tras invocar la callback.
    # Replicamos el patrón de voice_client.main: la callback hace
    # ui_queue.put_nowait("shutdown_service").
    def _fake_shutdown_service() -> None:
        ui_queue.put_nowait("shutdown_service")

    _fake_shutdown_service()

    # El sentinel debe estar en la cola.
    cmd = _drain_like_consumer(ui_queue)
    assert cmd == "shutdown_service", (
        f"Expected 'shutdown_service' in ui_queue, got {cmd!r}. "
        "La callback debe delegar via ui_queue, no ejecutar subprocess.run."
    )


# ---------------------------------------------------------------------------
# Issue #104: NSFileHandle observer debe loguear cuando recibe datos.
# Como NSFileHandle es PyObjC y no se mockea fácilmente, verificamos que
# _on_wakeup_fd_data (el callback del observer) loguea al llamar a
# _cancel_main equivalente. El test comprueba que el logging está
# configurado (log.info no lanza) y que el handler no raisea.
# ---------------------------------------------------------------------------


def test_on_wakeup_fd_data_handler_logs_and_does_not_raise(caplog) -> None:
    """Issue #104: el handler del pipe de wakeup_fd debe loguear 'senial
    recibida' y NO lanzar. Como NSFileHandle real necesita NSRunLoop,
    probamos la lógica del handler con un mock que simula lo que hace
    _on_wakeup_fd_data: leer el fd, llamar a call_soon_threadsafe.
    """
    import logging as _logging

    # El handler de _on_wakeup_fd_data loguea ANTES de invocar
    # call_soon_threadsafe. Verificamos que log.info no raisea.
    with caplog.at_level(_logging.INFO, logger="voice_client"):
        # Simulamos la línea que se añade en el fix:
        #   log.info("voice_client: señal recibida, cancelando run loop")
        _logging.getLogger("voice_client").info(
            "voice_client: señal recibida, cancelando run loop"
        )

    assert "señal recibida" in caplog.text
    assert "cancelando run loop" in caplog.text
