"""Tests offline de MenubarClient: mapeo de eventos y drenaje de cola."""
from __future__ import annotations

import asyncio
import os
import queue
import signal
from unittest.mock import patch

from domain.event_bus import EventBus, VoiceEvent
from ui.menubar_base import MenubarCommands
from ui.menubar_client import MenubarClient

from tests.fakes.waiting import wait_until


class FakeBackend:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def set_state(self, state: str) -> None:
        self.calls.append(("set_state", (state,)))

    def set_status_text(self, text: str) -> None:
        self.calls.append(("set_status_text", (text,)))

    def set_listening_paused(self, paused: bool) -> None:
        self.calls.append(("set_listening_paused", (paused,)))

    def set_errors_count(self, n: int) -> None:
        self.calls.append(("set_errors_count", (n,)))

    def set_commands(self, cmds: MenubarCommands) -> None:
        self.calls.append(("set_commands", (id(cmds),)))

    def set_health(self, level: str, text: str) -> None:
        self.calls.append(("set_health", (level, text)))


def _run(coro):
    return asyncio.run(coro)


async def _subscribed(bus: EventBus) -> None:
    """Espera a que MenubarClient.run() tenga su cola registrada en el bus
    (antes de eso, publish() es un no-op). Sustituye al sleep(0.05) fijo
    (issue #76)."""
    await wait_until(lambda: bool(bus._queues), message="suscripción al bus")


class _GatedSleep:
    """Sustituto de asyncio.sleep: registra los delays > 0 y no vuelve hasta
    que el test abre ``release``. Los sleep(0) (ceder el loop) pasan."""

    def __init__(self) -> None:
        self._real_sleep = asyncio.sleep
        self.requested: list[float] = []
        self.release = asyncio.Event()

    async def __call__(self, delay: float, result=None):
        if delay <= 0:
            return await self._real_sleep(0, result)
        self.requested.append(delay)
        await self.release.wait()
        return result


def test_state_changed_propagates():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(bus, ui_queue, pause, loop, stop))
        await _subscribed(bus)
        bus.publish(VoiceEvent(type="state_changed", data={"state": "listening"}))
        bus.publish(VoiceEvent(type="transcription", data={"text": "hola"}))
        # Sin espera: bus.close() encola el sentinel detrás de los eventos.
        bus.close()
        await asyncio.wait_for(task, timeout=5.0)

    _run(scenario())

    methods = [c[0] for c in backend.calls]
    assert "set_state" in methods
    assert "set_status_text" in methods
    set_state_vals = [c[1][0] for c in backend.calls if c[0] == "set_state"]
    assert "listening" in set_state_vals
    set_text_vals = [c[1][0] for c in backend.calls if c[0] == "set_status_text"]
    assert "hola" in set_text_vals


def test_wake_word_detected_sets_listening_instantly():
    """El icono debe reaccionar en el instante en que se detecta la wake
    word, no esperar a un state_changed (que la state machine actual NO
    emite para "listening" — solo para "idle")."""
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(bus, ui_queue, pause, loop, stop))
        await _subscribed(bus)
        bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "hey_jarvis"}))
        # Sin espera: bus.close() encola el sentinel detrás de los eventos.
        bus.close()
        await asyncio.wait_for(task, timeout=5.0)

    _run(scenario())

    set_state_vals = [c[1][0] for c in backend.calls if c[0] == "set_state"]
    assert set_state_vals == ["listening"]


def test_recording_ended_sets_thinking():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(bus, ui_queue, pause, loop, stop))
        await _subscribed(bus)
        bus.publish(VoiceEvent(type="recording_ended", data={}))
        # Sin espera: bus.close() encola el sentinel detrás de los eventos.
        bus.close()
        await asyncio.wait_for(task, timeout=5.0)

    _run(scenario())

    set_state_vals = [c[1][0] for c in backend.calls if c[0] == "set_state"]
    assert set_state_vals == ["thinking"]


def test_playback_started_sets_speaking():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(bus, ui_queue, pause, loop, stop))
        await _subscribed(bus)
        bus.publish(VoiceEvent(type="playback_started", data={}))
        # Sin espera: bus.close() encola el sentinel detrás de los eventos.
        bus.close()
        await asyncio.wait_for(task, timeout=5.0)

    _run(scenario())

    set_state_vals = [c[1][0] for c in backend.calls if c[0] == "set_state"]
    assert set_state_vals == ["speaking"]


def test_error_also_sets_error_state():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(bus, ui_queue, pause, loop, stop))
        await _subscribed(bus)
        bus.publish(VoiceEvent(type="error", data={"message": "boom"}))
        # Sin espera: bus.close() encola el sentinel detrás de los eventos.
        bus.close()
        await asyncio.wait_for(task, timeout=5.0)

    _run(scenario())

    set_state_vals = [c[1][0] for c in backend.calls if c[0] == "set_state"]
    assert set_state_vals == ["error"]


def test_error_increments_count():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(bus, ui_queue, pause, loop, stop))
        await _subscribed(bus)
        bus.publish(VoiceEvent(type="error", data={"message": "boom"}))
        bus.publish(VoiceEvent(type="error", data={"message": "boom2"}))
        # Sin espera: bus.close() encola el sentinel detrás de los eventos.
        bus.close()
        await asyncio.wait_for(task, timeout=5.0)

    _run(scenario())
    err_vals = sorted(c[1][0] for c in backend.calls if c[0] == "set_errors_count")
    assert err_vals == [1, 2]


def test_toggle_pause_flips_event():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()
    # simulate UI thread pushing commands
    ui_queue.put_nowait("toggle_pause")
    ui_queue.put_nowait("toggle_pause")

    async def scenario():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(bus, ui_queue, pause, loop, stop))
        ui_queue.put_nowait("toggle_pause")
        # El hilo del executor drena la cola a su ritmo: esperar a los tres
        # toggles en vez de dormir un tiempo fijo (issue #76).
        await wait_until(
            lambda: sum(1 for c in backend.calls if c[0] == "set_listening_paused") == 3,
            message="tres toggle_pause procesados",
        )
        bus.close()
        await asyncio.wait_for(task, timeout=5.0)

    _run(scenario())

    # After three toggles: set, clear, set -> paused=True
    assert pause.is_set()
    paused_calls = [c[1][0] for c in backend.calls if c[0] == "set_listening_paused"]
    assert paused_calls == [True, False, True]


def test_cancelled_event_sets_cancelled_state():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(bus, ui_queue, pause, loop, stop))
        await _subscribed(bus)
        bus.publish(VoiceEvent(type="cancelled", data={}))
        # Sin espera: bus.close() encola el sentinel detrás de los eventos.
        bus.close()
        await asyncio.wait_for(task, timeout=5.0)

    _run(scenario())

    set_state_vals = [c[1][0] for c in backend.calls if c[0] == "set_state"]
    assert set_state_vals == ["cancelled"]


def test_error_state_holds_before_reverting_to_idle():
    """Sin hold, state_changed("idle") (publicado por _idle() en la siguiente
    iteración del loop) sobreescribe el icono de error casi al instante,
    haciéndolo imperceptible. Con hold, "idle" debe demorarse hasta que
    pase la ventana de hold desde el último estado transitorio (error o
    cancelled)."""
    bus = EventBus()
    backend = FakeBackend()
    # Issue #76: antes usaba una ventana real de 0.15s y comprobaba a los
    # 0.05s y a los 0.25s, frágil con la CPU cargada. Ahora la ventana es
    # enorme y el sleep del idle diferido lo libera el propio test.
    client = MenubarClient(backend, hold_transient_s=30.0)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()
    gated = _GatedSleep()

    async def scenario():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(bus, ui_queue, pause, loop, stop))
        await _subscribed(bus)
        bus.publish(VoiceEvent(type="error", data={"message": "boom"}))
        bus.publish(VoiceEvent(type="state_changed", data={"state": "idle"}))
        await wait_until(lambda: bool(gated.requested), message="idle diferido programado")
        # Aún dentro de la ventana de hold: debe seguir mostrando "error".
        assert backend.calls[-1] == ("set_state", ("error",)), backend.calls
        # El idle se difiere lo que queda de la ventana (casi 30s).
        assert 29.0 < gated.requested[0] <= 30.0, gated.requested
        gated.release.set()
        # Pasada la ventana de hold: debe haber revertido a "idle".
        await wait_until(
            lambda: backend.calls[-1] == ("set_state", ("idle",)),
            message=f"revertir a idle; calls={backend.calls}",
        )
        bus.close()
        await asyncio.wait_for(task, timeout=5.0)

    with patch.object(asyncio, "sleep", gated):
        _run(scenario())


def test_new_real_event_during_hold_overrides_pending_idle():
    """Si llega un evento real (p.ej. una nueva wake word) mientras el
    icono de error/cancelled está retenido, debe mostrarse de inmediato —
    no debe quedar oculto esperando a que expire el hold ni a que el idle
    diferido se aplique por encima."""
    bus = EventBus()
    backend = FakeBackend()
    # Ventana de hold muy superior a lo que dura el test: si el idle
    # diferido llegara a aplicarse sería un bug, no lentitud (issue #76).
    client = MenubarClient(backend, hold_transient_s=30.0)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(bus, ui_queue, pause, loop, stop))
        await _subscribed(bus)
        bus.publish(VoiceEvent(type="error", data={"message": "boom"}))
        bus.publish(VoiceEvent(type="state_changed", data={"state": "idle"}))
        await wait_until(
            lambda: client._pending_idle_task is not None, message="idle diferido programado"
        )
        pending = client._pending_idle_task
        bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_jota"}))
        await wait_until(
            lambda: backend.calls[-1] == ("set_state", ("listening",)),
            message=f"listening inmediato; calls={backend.calls}",
        )
        await wait_until(lambda: pending.done(), message="idle diferido cancelado")
        assert pending.cancelled()
        assert ("set_state", ("idle",)) not in backend.calls, backend.calls
        bus.close()
        await asyncio.wait_for(task, timeout=5.0)

    _run(scenario())


def test_run_returns_on_bus_close():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(bus, ui_queue, pause, loop, stop))
        await _subscribed(bus)
        bus.close()
        await asyncio.wait_for(task, timeout=5.0)
        return task.result()

    _run(scenario())


def test_delayed_idle_race_does_not_clobber_later_task():
    """Issue #31: si la tarea A (_delayed_idle) ya está dormida en
    asyncio.sleep y en el mismo tick síncrono se crea/asigna tarea B a
    _pending_idle_task, cuando la cancelación de A se entrega su finally
    NO debe borrar la referencia a B.

    Escenario real: dos errores dentro de la ventana hold_transient_s,
    seguidos de una wake word. El icono no debe mostrar "IDLE" espurio.
    """
    backend = FakeBackend()
    # Delays largos: antes A dormía 0.05s y el test cedía 0.01s reales para
    # que A llegara al sleep; con la CPU cargada A podía terminar antes de
    # comprobar que seguía viva (issue #76). Un solo tick basta.
    client = MenubarClient(backend, hold_transient_s=30.0)

    async def scenario():
        # Crear task A y asignarla manualmente a _pending_idle_task
        # (asi es como _set_idle_holding_transients lo hace internamente)
        task_a = asyncio.create_task(client._delayed_idle(30.0))
        client._pending_idle_task = task_a

        # Ceder el control una vez para que A alcance await asyncio.sleep
        await asyncio.sleep(0)

        # Confirmar que A está viva y dentro del sleep
        assert not task_a.done(), "task A should be in sleep"

        # En el mismo tick síncrono: crear task B y asignarla a _pending_idle_task.
        # Esto reproduce el caso en que otra código (ej. otro error) reemplazó la
        # referencia antes de que la cancelación de A se entregue.
        task_b = asyncio.create_task(client._delayed_idle(30.0))
        client._pending_idle_task = task_b

        # Cancelar task A directamente (no a través de _cancel_pending_idle,
        # para evitar que también haga _pending_idle_task = None)
        task_a.cancel()

        # Esperar a que la cancelación se entregue y el finally de A corra
        try:
            await task_a
        except asyncio.CancelledError:
            pass

        # El finally de A debe haber respetado a B: _pending_idle_task sigue siendo B
        assert client._pending_idle_task is task_b, (
            f"Expected _pending_idle_task to be task_b, got {client._pending_idle_task}"
        )
        # Y B sigue viva (no fue cancelada)
        assert not task_b.done(), "task B should still be alive"
        task_b.cancel()

    _run(scenario())


def test_shutdown_service_sends_sigterm_to_self():
    """Issue #30: shutdown_service debe enviar SIGTERM al proceso actual
    vía os.kill(getpid(), SIGTERM), no usar launchctl disable/bootout
    (que con KeepAlive=true en macOS Sonoma no previene el respawn).
    No podemos matar realmente el proceso de test, así que mockeamos
    os.kill y verificamos que se llama con el PID real + SIGTERM."""
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()
    stop = asyncio.Event()

    killed = {}

    def fake_kill(pid, sig):
        killed["pid"] = pid
        killed["sig"] = sig

    async def scenario():
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(client.run(bus, ui_queue, pause, loop, stop))
        await _subscribed(bus)
        ui_queue.put_nowait("shutdown_service")
        # El comando lo recoge el hilo del executor: esperar a que fake_kill
        # se llame en vez de dormir 0.2s fijos (issue #76).
        await wait_until(lambda: "pid" in killed, message="os.kill llamado")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    with patch.object(os, "kill", fake_kill):
        with patch.object(signal, "SIGTERM", signal.SIGTERM):
            _run(scenario())

    assert "pid" in killed, f"os.kill never called; killed={killed}"
    assert killed["pid"] == os.getpid(), (
        f"Expected kill(getpid()={os.getpid()}), got pid={killed['pid']}"
    )
    assert killed["sig"] == signal.SIGTERM, (
        f"Expected SIGTERM={signal.SIGTERM}, got sig={killed['sig']}"
    )


def test_only_queue_commands_are_dispatched(caplog):
    """Issue #99: por ui_queue solo pasan los comandos que tocan estado de
    asyncio (toggle_pause y shutdown_service). open_logs, open_config y quit
    son callbacks directos en voice_client; sus ramas en la cola eran código
    muerto y ahora caen en "comando UI desconocido"."""
    import logging

    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(bus, ui_queue, pause, loop, stop))
        await _subscribed(bus)
        for cmd in ("open_logs", "open_config", "quit"):
            ui_queue.put_nowait(cmd)
        await wait_until(
            lambda: sum("desconocido" in r.getMessage() for r in caplog.records) == 3,
            message="tres comandos desconocidos",
        )
        bus.close()
        await asyncio.wait_for(task, timeout=5.0)

    with caplog.at_level(logging.INFO, logger="ui.menubar_client"):
        _run(scenario())

    assert not any("requested" in r.getMessage() for r in caplog.records)


class _RecordingQueue(queue.Queue):
    """queue.Queue que registra los timeouts con los que se llama a get()."""

    def __init__(self):
        super().__init__()
        self.get_timeouts: list = []

    def get(self, block=True, timeout=None):
        self.get_timeouts.append(timeout)
        return super().get(block, timeout)


def _forwarder_threads():
    import threading

    return [t for t in threading.enumerate() if t.name == "menubar-ui-commands"]


def test_ui_queue_drained_without_polling_nor_default_executor():
    """Issue #100: antes se sondeaba ui_queue cada 200 ms con
    run_in_executor(None, ...), ocupando para siempre un hilo del executor
    por defecto (compartido con audio/HTTP). Ahora un hilo propio bloquea en
    get() sin timeout y reenvía con call_soon_threadsafe; al terminar run()
    el hilo se desbloquea y sale."""
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue = _RecordingQueue()
    pause = asyncio.Event()

    async def scenario():
        loop = asyncio.get_running_loop()

        def _no_executor(*_args, **_kwargs):
            raise AssertionError("MenubarClient no debe usar el executor por defecto")

        loop.run_in_executor = _no_executor
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(bus, ui_queue, pause, loop, stop))
        await _subscribed(bus)
        ui_queue.put_nowait("toggle_pause")
        await wait_until(lambda: pause.is_set(), message="toggle_pause procesado")
        assert len(_forwarder_threads()) == 1
        bus.close()
        await asyncio.wait_for(task, timeout=5.0)
        await wait_until(lambda: not _forwarder_threads(), message="hilo reenviador terminado")

    _run(scenario())

    assert ui_queue.get_timeouts, "get() nunca se llamó"
    assert all(t is None for t in ui_queue.get_timeouts), ui_queue.get_timeouts
    assert backend.calls == [("set_listening_paused", (True,))]


def test_forwarder_thread_exits_when_run_is_cancelled():
    bus = EventBus()
    client = MenubarClient(FakeBackend())
    ui_queue: queue.Queue = queue.Queue()

    async def scenario():
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(
            client.run(bus, ui_queue, asyncio.Event(), loop, asyncio.Event())
        )
        await _subscribed(bus)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await wait_until(lambda: not _forwarder_threads(), message="hilo reenviador terminado")

    _run(scenario())


# ---------------------------------------------------------------------------
# Issues #108 y #7: estado del gateway y de la wake word en el menubar.
# ---------------------------------------------------------------------------


def _health_after(*events: VoiceEvent) -> tuple[FakeBackend, list[tuple[str, str]]]:
    """Publica ``events`` en orden y devuelve las llamadas a set_health."""
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend, hold_transient_s=0.0)
    ui_queue: queue.Queue = queue.Queue()

    async def scenario():
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(
            client.run(bus, ui_queue, asyncio.Event(), loop, asyncio.Event())
        )
        await _subscribed(bus)
        for ev in events:
            bus.publish(ev)
        bus.close()
        await asyncio.wait_for(task, timeout=5.0)

    _run(scenario())
    return backend, [c[1] for c in backend.calls if c[0] == "set_health"]


def _status(service: str, state: str, **extra) -> VoiceEvent:
    # Forma real: el gateway reenvía el mensaje íntegro (con type="status").
    return VoiceEvent(
        type="gateway_status",
        data={"type": "status", "service": service, "state": state, **extra},
    )


def _gw(state: str, **extra) -> VoiceEvent:
    # Forma de gateway_session._status() (supervisor de la sesión).
    return VoiceEvent(type="gateway_status", data={"service": "gateway", "state": state, **extra})


def _fatal(kind: str = "invalid_client_key") -> VoiceEvent:
    # Forma de gateway_session.handshake_error_event().
    return VoiceEvent(type="error", data={
        "code": "GATEWAY_HANDSHAKE_REJECTED",
        "kind": kind,
        "close_code": 1008,
        "message": "Gateway rechazó el handshake (1008): invalid client key",
        "fatal": True,
    })


def test_transcriber_degraded_is_discreet():
    """jota-gateway#227 manda "transcriber degraded" falsos en reposo: el
    aviso es informativo, sin icono de error ni contador de errores."""
    backend, health = _health_after(_status("transcriber", "degraded"))

    assert health == [("info", "Transcripción degradada")]
    assert not [c for c in backend.calls if c[0] in ("set_state", "set_errors_count")]


def test_transcriber_degraded_cleared_by_restored():
    _, health = _health_after(
        _status("transcriber", "degraded"), _status("transcriber", "restored")
    )
    assert health[-1] == ("ok", "")


def test_transcriber_degraded_cleared_by_next_good_transcription():
    _, health = _health_after(
        _status("transcriber", "degraded"),
        VoiceEvent(type="transcription", data={"text": "enciende la luz"}),
    )
    assert health[-1] == ("ok", "")


def test_empty_transcription_does_not_clear_degraded():
    _, health = _health_after(
        _status("transcriber", "degraded"),
        VoiceEvent(type="transcription", data={"text": ""}),
    )
    assert health == [("info", "Transcripción degradada")]


def test_repeated_status_is_not_resent():
    _, health = _health_after(
        _status("transcriber", "degraded"), _status("transcriber", "degraded")
    )
    assert health == [("info", "Transcripción degradada")]


def test_transcriber_unavailable_is_a_warning():
    _, health = _health_after(_status("transcriber", "unavailable"))
    assert health == [("warn", "Transcripción no disponible")]


def test_orchestrator_reconnecting_then_restored():
    _, health = _health_after(
        _status("orchestrator", "reconnecting"), _status("orchestrator", "restored")
    )
    assert health == [("warn", "Orquestador: reconectando…"), ("ok", "")]


def test_gateway_reconnecting_then_connected():
    _, health = _health_after(
        _gw("reconnecting", attempt=1, delay_s=1.0), _gw("connected", session_id="s2")
    )
    assert health == [("warn", "Gateway: reconectando…"), ("ok", "")]


def test_gateway_disconnected_supersedes_service_statuses():
    """Con la sesión caída, los status de servicios de la sesión anterior ya
    no significan nada: se descartan."""
    _, health = _health_after(
        _status("transcriber", "degraded"), _gw("disconnected", code=1006, reason="")
    )
    assert health[-1] == ("warn", "Gateway: desconectado")


def test_non_fatal_disconnect_error_shows_no_connection_until_ready():
    _, health = _health_after(
        VoiceEvent(type="error", data={
            "code": "GATEWAY_DISCONNECTED", "message": "x", "fatal": False,
        }),
        VoiceEvent(type="gateway_ready", data={"session_id": "s", "agent": "a", "capabilities": {}}),
    )
    assert health == [("warn", "Gateway: sin conexión"), ("ok", "")]


def test_fatal_handshake_rejection_is_clearly_visible():
    backend, health = _health_after(_fatal("invalid_client_key"))

    assert health == [("fatal", "Gateway: client_key inválida")]
    # Sigue contando como error y mostrando el icono de error transitorio.
    assert ("set_errors_count", (1,)) in backend.calls
    assert ("set_state", ("error",)) in backend.calls


def test_fatal_survives_reconnecting_status_and_clears_on_ready():
    _, health = _health_after(
        _fatal("invalid_client_key"),
        _gw("reconnecting", attempt=1, delay_s=1.0),
        VoiceEvent(type="gateway_ready", data={"session_id": "s", "agent": "a", "capabilities": {}}),
    )
    assert health[0] == ("fatal", "Gateway: client_key inválida")
    assert health[1][0] == "fatal"
    assert health[-1] == ("ok", "")


def test_other_fatal_gateway_error_is_shown():
    _, health = _health_after(VoiceEvent(type="error", data={
        "code": "TURN_ERROR", "message": "agente caído", "fatal": True,
    }))
    assert health == [("fatal", "Error grave: agente caído")]


def test_non_fatal_error_does_not_touch_health():
    _, health = _health_after(VoiceEvent(type="error", data={"message": "boom"}))
    assert health == []


def test_wake_word_degraded_and_recovered():
    _, health = _health_after(
        VoiceEvent(type="oww_degraded", data={"consecutive_failures": 3, "backoff_s": 5.0}),
        VoiceEvent(type="oww_recovered", data={}),
    )
    assert health == [("warn", "Wake word no disponible"), ("ok", "")]


def test_several_conditions_are_combined_by_severity():
    _, health = _health_after(
        _status("transcriber", "degraded"),
        VoiceEvent(type="oww_degraded", data={"consecutive_failures": 3, "backoff_s": 5.0}),
    )
    assert health[-1] == ("warn", "Wake word no disponible · Transcripción degradada")


def test_malformed_status_is_ignored():
    _, health = _health_after(
        VoiceEvent(type="gateway_status", data={"type": "status"}),
        _status("transcriber", "algo_nuevo"),
    )
    assert health == []


def test_good_turn_clears_orchestrator_and_tts_warnings():
    _, health = _health_after(
        _status("orchestrator", "unavailable"),
        _status("tts", "unavailable"),
        VoiceEvent(type="llm_token", data={"content": "Hola"}),
        VoiceEvent(type="playback_started", data={}),
    )
    assert health[-1] == ("ok", "")
    assert ("warn", "Orquestador no disponible · Síntesis de voz no disponible") in health
