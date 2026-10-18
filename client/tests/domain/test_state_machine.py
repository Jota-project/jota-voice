"""
test_state_machine.py — Test offline del ciclo IDLE→RECORDING→RESPONDING→IDLE.

Usa mocks de todos los módulos; no requiere hardware ni servicios externos.
Verifica que el EventBus recibe los eventos correctos en el orden correcto
para un ciclo completo.

Ejecutar:
    python -m pytest client/test_state_machine.py -v
    python client/test_state_machine.py
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncGenerator
from unittest.mock import MagicMock

import pytest

# pyaudio solo existe en Termux: el módulo falso lo pone el fixture
# ``fake_pyaudio`` de tests/conftest.py (con teardown), en vez del antiguo
# _install_stubs() que tocaba sys.modules al importar este fichero.
pytestmark = pytest.mark.usefixtures("fake_pyaudio")

# Importar módulos del proyecto
from domain.event_bus import EventBus, VoiceEvent                       # noqa: E402
from config import Config, GatewayConfig, AudioConfig, OWWConfig, DeviceConfig  # noqa: E402
from backends.gateway_client import GatewayClient, GatewayEvent         # noqa: E402
from backends.audio_base import AudioBackend                            # noqa: E402
from backends.audio_sounddevice import SounddeviceBackend               # noqa: E402
from backends.oww_client import OWWClient                               # noqa: E402
from app.playback_engine import PlaybackEngine                          # noqa: E402


# ---------------------------------------------------------------------------
# Helpers de mock
# ---------------------------------------------------------------------------

def _make_config(
    *,
    persistent_session: bool = True,
    **kwargs,
) -> Config:
    return Config(
        gateway=GatewayConfig(
            host="127.0.0.1",
            client_key="test",
            connect_timeout_s=5.0,
            persistent_session=persistent_session,
        ),
        device=DeviceConfig(id="test-device"),
        audio=AudioConfig(
            sample_rate=16000,
            frames_per_buffer=512,
            silence_timeout_s=0.1,   # ~3 frames de silencio con silence_count
            # Cota superior de seguridad: con _make_audio_mock el turno acaba
            # por silencio en ~3 frames y nunca llega a este plazo (#112).
            recording_timeout_s=5.0,
        ),
        oww=OWWConfig(),
        **kwargs,
    )


def _make_audio_mock(*, silent: bool = True) -> MagicMock:
    """
    Mock de AudioBackend.
    - get_queue() devuelve una Queue que se rellena con 20 frames float32
      justo DESPUÉS del drain de _recording(): get_preroll() se llama tras el
      drain y antes de _capture_loop, y su side_effect encola los frames.
      Antes la cola se llenaba al crear el mock, IDLE/RECORDING la drenaban y
      cada turno agotaba recording_timeout_s (5 s reales por test, #112).
    - get_preroll() devuelve bytes vacíos.
    - is_silence() devuelve `silent` para todos los frames.
    """
    audio = MagicMock(spec=AudioBackend)

    q: asyncio.Queue[bytes] = asyncio.Queue()
    frame = bytes(512 * 4)  # 512 float32 samples = 2048 bytes de ceros

    def _preroll_then_frames() -> bytes:
        for _ in range(20):
            q.put_nowait(frame)
        return b""

    audio.get_queue.return_value = q
    audio.get_preroll.side_effect = _preroll_then_frames
    audio.is_silence.return_value = silent
    return audio


def _make_oww_mock(wake_word: str = "ok_nabu") -> MagicMock:
    """Mock de OWWClient que detecta el wake word al primer intento."""
    oww = MagicMock(spec=OWWClient)
    oww.is_connected = True
    oww.wait_for_detection.return_value = wake_word
    return oww


def _gateway_mock_with_events(events: list[GatewayEvent]) -> MagicMock:
    """Mock de GatewayClient que emite una lista de GatewayEvents y luego cierra."""

    async def _receive() -> AsyncGenerator[GatewayEvent, None]:
        for ev in events:
            yield ev

    gateway = MagicMock(spec=GatewayClient)
    gateway.receive = _receive
    return gateway


def _make_playback_mock() -> MagicMock:
    return MagicMock(spec=PlaybackEngine)


def _publish_wake_on_idle(bus: EventBus, times: int = 1) -> asyncio.Task:
    """Publica wake_word_detected cada vez que la máquina entra en IDLE, hasta
    ``times`` veces. Se suscribe al llamarla (antes de arrancar la máquina),
    así que no pierde el primer state_changed(idle). Sustituye a los bucles
    de sondeo con asyncio.sleep (#76)."""
    events = bus.subscribe()

    async def _run() -> None:
        published = 0
        async with contextlib.aclosing(events):
            async for ev in events:
                if ev.type == "state_changed" and ev.data.get("state") == "idle":
                    bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))
                    published += 1
                    if published >= times:
                        return

    return asyncio.create_task(_run())


def _wait_for_event(bus: EventBus, type_: str) -> asyncio.Task:
    """Task que termina cuando se publica un evento ``type_``. Se suscribe al
    llamarla, así que no pierde eventos publicados después."""
    events = bus.subscribe()

    async def _run() -> VoiceEvent:
        async with contextlib.aclosing(events):
            async for ev in events:
                if ev.type == type_:
                    return ev
        raise AssertionError(f"el bus se cerró sin publicar {type_!r}")

    return asyncio.create_task(_run())


# ---------------------------------------------------------------------------
# Test 1: ciclo E2E completo IDLE → RECORDING → RESPONDING → IDLE
# ---------------------------------------------------------------------------

async def _run_e2e_test() -> None:
    print("\n=== Test E2E: ciclo completo IDLE→RECORDING→RESPONDING→IDLE ===")

    bus = EventBus()
    cfg = _make_config()

    # Gateway devuelve: transcription_partial, transcription, dos llm_token, tts_chunk
    gw_events = [
        GatewayEvent(type="transcription_partial", data={"text": "¿qué hora"}),
        GatewayEvent(type="transcription",         data={"text": "¿qué hora es?"}),
        GatewayEvent(type="llm_token",             data={"content": "Son "}),
        GatewayEvent(type="llm_token",             data={"content": "las 12."}),
        GatewayEvent(type="tts_chunk",             data={"audio": b"\x00\x01" * 100}),
    ]

    audio    = _make_audio_mock(silent=True)
    oww      = _make_oww_mock(wake_word="ok_nabu")
    gateway  = _gateway_mock_with_events(gw_events)
    playback = _make_playback_mock()

    # El state_machine consume `wake_word_detected` del bus. Publicamos el
    # primer wake_word inmediatamente; los siguientes nunca llegan (simula
    # que OWW solo detectó una vez y luego está en silencio).
    wake_publisher_task = _publish_wake_on_idle(bus)

    # Suscribir al bus para capturar todos los eventos
    received: list[VoiceEvent] = []

    async def _collector() -> None:
        async for ev in bus.subscribe():
            received.append(ev)

    collector_task = asyncio.create_task(_collector())

    # Parar cuando llegue el segundo state_changed(idle)
    stop_event = asyncio.Event()
    idle_count = [0]

    async def _watcher() -> None:
        async for ev in bus.subscribe():
            if ev.type == "state_changed" and ev.data.get("state") == "idle":
                idle_count[0] += 1
                if idle_count[0] >= 2:
                    stop_event.set()
                    return

    watcher_task = asyncio.create_task(_watcher())

    from domain.state_machine import run as sm_run

    sm_task = asyncio.create_task(sm_run(cfg, bus, audio, gateway, playback))

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        pass

    sm_task.cancel()
    watcher_task.cancel()
    wake_publisher_task.cancel()
    try:
        await sm_task
    except asyncio.CancelledError:
        pass
    try:
        await watcher_task
    except asyncio.CancelledError:
        pass
    try:
        await wake_publisher_task
    except asyncio.CancelledError:
        pass

    bus.close()
    try:
        await collector_task
    except Exception:
        pass

    # -----------------------------------------------------------------------
    # Verificaciones
    # -----------------------------------------------------------------------
    event_types = [e.type for e in received]
    print(f"\nEventos capturados ({len(received)} total):")
    for i, ev in enumerate(received):
        print(f"  [{i:02d}] {ev.type}  data={ev.data}")

    # Secuencia mínima obligatoria para un ciclo completo.
    # (state_machine no publica tts_chunk al bus, solo lo pasa a playback.play_chunk)
    required_sequence = [
        "state_changed",          # idle (primer ciclo)
        "wake_word_detected",
        "recording_started",
        "recording_ended",
        "transcription_partial",
        "transcription",
        "llm_token",
        "playback_started",
        "playback_ended",
        "state_changed",          # idle (segundo ciclo)
    ]

    print(f"\nVerificando secuencia mínima de {len(required_sequence)} eventos…")
    idx = 0
    for expected in required_sequence:
        found = False
        while idx < len(event_types):
            if event_types[idx] == expected:
                found = True
                idx += 1
                break
            idx += 1
        if not found:
            raise AssertionError(
                f"Evento esperado {expected!r} no encontrado en secuencia.\n"
                f"Recibidos: {event_types}"
            )
        print(f"  OK  {expected}")

    # Verificar contenidos concretos
    ww_ev = next(e for e in received if e.type == "wake_word_detected")
    assert ww_ev.data["wake_word"] == "ok_nabu", f"Wake word incorrecto: {ww_ev.data}"
    print("  OK  wake_word_detected data correcto")

    transcript_ev = next(e for e in received if e.type == "transcription")
    assert transcript_ev.data["text"] == "¿qué hora es?", f"Transcripción incorrecta: {transcript_ev.data}"
    print("  OK  transcription data correcto")

    llm_evs = [e for e in received if e.type == "llm_token"]
    assert len(llm_evs) == 2, f"Esperaba 2 llm_token, recibí {len(llm_evs)}"
    combined = "".join(e.data["content"] for e in llm_evs)
    assert combined == "Son las 12.", f"LLM tokens incorrectos: {combined!r}"
    print(f"  OK  llm_token (2 tokens): {combined!r}")

    # Verificar que play_chunk fue llamado con los bytes del tts_chunk
    assert playback.play_chunk.await_count >= 1, "PlaybackEngine.play_chunk nunca fue llamado"
    assert playback.drain.await_count >= 1,      "PlaybackEngine.drain nunca fue llamado"
    assert playback.reset.call_count >= 1,       "PlaybackEngine.reset nunca fue llamado"
    print("  OK  playback: play_chunk / drain / reset llamados")

    # ≥2 state_changed("idle")
    idle_evs = [e for e in received if e.type == "state_changed" and e.data.get("state") == "idle"]
    assert len(idle_evs) >= 2, f"Esperaba ≥2 state_changed(idle), recibí {len(idle_evs)}"
    print(f"  OK  state_changed(idle) × {len(idle_evs)}")

    print("\nTest E2E: PASADO")


# ---------------------------------------------------------------------------
# Test 2: error en RECORDING (gateway.connect() falla)
# ---------------------------------------------------------------------------

async def _run_error_test() -> None:
    print("\n=== Test error: fallo en gateway.connect() ===")

    bus = EventBus()
    # Explicitly use ephemeral mode so connect() IS called and can fail.
    cfg = _make_config(persistent_session=False)
    audio = _make_audio_mock(silent=True)

    oww = _make_oww_mock()

    # Publica wake_word_detected en el bus (el state_machine lo consume desde ahí).
    wake_publisher_task = _publish_wake_on_idle(bus)

    gateway = MagicMock(spec=GatewayClient)
    gateway.connect.side_effect = ConnectionRefusedError("gateway no disponible")
    playback = _make_playback_mock()

    received: list[VoiceEvent] = []
    stop_event = asyncio.Event()

    async def _collector() -> None:
        async for ev in bus.subscribe():
            received.append(ev)
            if ev.type == "error":
                stop_event.set()

    collector_task = asyncio.create_task(_collector())

    from domain.state_machine import run as sm_run

    sm_task = asyncio.create_task(sm_run(cfg, bus, audio, gateway, playback))

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        pass

    sm_task.cancel()
    wake_publisher_task.cancel()
    try:
        await sm_task
    except asyncio.CancelledError:
        pass
    try:
        await wake_publisher_task
    except asyncio.CancelledError:
        pass

    bus.close()
    try:
        await collector_task
    except Exception:
        pass

    error_evs = [e for e in received if e.type == "error"]
    assert len(error_evs) >= 1, f"Esperaba ≥1 evento error, recibí {len(error_evs)}"
    assert "gateway no disponible" in error_evs[0].data["message"], (
        f"Mensaje de error incorrecto: {error_evs[0].data['message']!r}"
    )
    print(f"  OK  error publicado: {error_evs[0].data['message']!r}")
    print("Test error: PASADO")


# ---------------------------------------------------------------------------
# Test 2b: play_notification() falla (dispositivo de audio no disponible) —
# no debe impedir gateway.send_end(). Visto en producción: un altavoz
# Bluetooth intermitente (PaErrorCode -9986) hacía fallar el beep de
# notificación, lo que abortaba la RECORDING entera ANTES de send_end(),
# dejando al gateway sin saber que el turno había terminado.
# ---------------------------------------------------------------------------

async def _run_notification_failure_test() -> None:
    print("\n=== Test: play_notification() falla, send_end() debe llamarse igual ===")

    bus = EventBus()
    cfg = _make_config()
    audio = _make_audio_mock(silent=True)

    wake_publisher_task = _publish_wake_on_idle(bus)

    gateway = _gateway_mock_with_events([])  # RESPONDING termina rápido (sin eventos)

    playback = _make_playback_mock()
    playback.play_notification.side_effect = RuntimeError(
        "Error opening OutputStream: Internal PortAudio error [PaErrorCode -9986]"
    )

    received: list[VoiceEvent] = []
    stop_event = asyncio.Event()
    idle_count = [0]

    async def _collector() -> None:
        async for ev in bus.subscribe():
            received.append(ev)
            if ev.type == "state_changed" and ev.data.get("state") == "idle":
                idle_count[0] += 1
                if idle_count[0] >= 2:
                    stop_event.set()

    collector_task = asyncio.create_task(_collector())

    from domain.state_machine import run as sm_run

    sm_task = asyncio.create_task(sm_run(cfg, bus, audio, gateway, playback))

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        pass

    sm_task.cancel()
    wake_publisher_task.cancel()
    for t in [sm_task, wake_publisher_task]:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    bus.close()
    try:
        await collector_task
    except Exception:
        pass

    event_types = [e.type for e in received]
    print(f"\nEventos: {event_types}")

    assert gateway.send_end.await_count >= 1, (
        f"gateway.send_end() no fue llamado tras el fallo de play_notification(): {event_types}"
    )
    assert "recording_ended" in event_types, (
        f"recording_ended no se publicó tras el fallo de play_notification(): {event_types}"
    )
    error_evs = [e for e in received if e.type == "error"]
    assert len(error_evs) >= 1, f"Esperaba visibilidad del fallo vía evento error: {event_types}"

    print("  OK  gateway.send_end() llamado pese al fallo del beep")
    print("  OK  recording_ended publicado")
    print("  OK  error publicado (visibilidad del fallo)")
    print("Test notification failure: PASADO")


# ---------------------------------------------------------------------------
# Test 2c: capture_task falla con una excepción real (p.ej. corte de red en
# gateway.send_audio) — a diferencia del fallo cosmético de play_notification
# de arriba, esta excepción SÍ debe propagarse: NO debe llamarse send_end()
# sobre una conexión rota ni publicarse recording_ended como si el turno
# hubiera terminado con normalidad.
# ---------------------------------------------------------------------------

async def _run_capture_exception_test() -> None:
    """capture_task falla con una excepción real (p.ej. corte de red a mitad de
    un gateway.send_audio). A diferencia del fallo cosmético de play_notification
    (test 2b), esta excepción SÍ debe propagar — si no, el código cae al camino
    "todo normal", intenta gateway.send_end() sobre una conexión rota, y la
    excepción real nunca se reporta con su mensaje original."""
    print("\n=== Test: excepción real de capture_task se propaga (no se silencia) ===")

    bus = EventBus()
    cfg = _make_config()
    audio = SounddeviceBackend(cfg.audio)
    audio._loop = asyncio.get_running_loop()
    audio._capture_q = asyncio.Queue()
    audio._oww_q = asyncio.Queue()

    gateway = _gateway_mock_with_events([])
    # Configuramos el mock para que la 3ª invocación (preroll vacío no
    # cuenta como send_audio → la 1ª va al preroll si existe; aquí no hay,
    # luego 2 sends en _capture_loop y el 3er lanza ConnectionError simulando
    # un corte de red a mitad de grabación).
    gateway.send_audio.side_effect = [None, None, ConnectionError("corte de red")]

    playback = _make_playback_mock()

    wake_publisher_task = _publish_wake_on_idle(bus)

    # Producer que introduce frames no silenciosos a la cola tras el drain
    # inicial de _recording() — sin esto, _capture_loop espera eternamente y
    # nunca llega a invocar gateway.send_audio(). Reacciona a
    # recording_started (publicado justo antes del drain síncrono): los
    # frames, encolados vía call_soon_threadsafe, llegan después del drain.
    recording_started = _wait_for_event(bus, "recording_started")

    async def _audio_producer() -> None:
        await recording_started
        for _ in range(20):
            audio._on_frame(_tone_frame_f32())

    audio_producer_task = asyncio.create_task(_audio_producer())

    received: list[VoiceEvent] = []
    stop_event = asyncio.Event()
    idle_count = [0]

    async def _collector() -> None:
        async for ev in bus.subscribe():
            received.append(ev)
            if ev.type == "state_changed" and ev.data.get("state") == "idle":
                idle_count[0] += 1
                if idle_count[0] >= 2:
                    stop_event.set()

    collector_task = asyncio.create_task(_collector())

    from domain.state_machine import run as sm_run

    sm_task = asyncio.create_task(sm_run(cfg, bus, audio, gateway, playback))

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        pass

    for t in (sm_task, wake_publisher_task, audio_producer_task):
        t.cancel()
    for t in (sm_task, wake_publisher_task, audio_producer_task):
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    bus.close()
    try:
        await collector_task
    except Exception:
        pass

    event_types = [e.type for e in received]
    print(f"\nEventos: {event_types}")

    assert gateway.send_end.await_count == 0, (
        f"gateway.send_end() no debía llamarse tras la excepción de capture_task: {event_types}"
    )
    assert "recording_ended" not in event_types, (
        f"recording_ended no debía publicarse tras la excepción de capture_task: {event_types}"
    )
    error_evs = [e for e in received if e.type == "error"]
    assert len(error_evs) >= 1, f"Esperaba visibilidad del fallo vía evento error: {event_types}"
    assert any("corte de red" in e.data["message"] for e in error_evs), (
        f"El mensaje real de la excepción de capture_task no se propagó: {[e.data for e in error_evs]}"
    )

    print("  OK  gateway.send_end() NO llamado tras la excepción de capture_task")
    print("  OK  recording_ended NO publicado")
    print("  OK  error publicado con el mensaje real de la excepción")
    print("Test capture exception: PASADO")


def test_state_machine_capture_exception_propagates() -> None:
    asyncio.run(_run_capture_exception_test())


# ---------------------------------------------------------------------------
# Test 3: timeout en IDLE — OWW nunca detecta, debe publicar error
# ---------------------------------------------------------------------------

async def _run_idle_timeout_test() -> None:
    print("\n=== Test IDLE timeout: OWW nunca detecta → error publicado ===")

    bus = EventBus()
    cfg = Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="test"),
        device=DeviceConfig(id="test-device"),
        oww=OWWConfig(idle_detection_timeout_s=0.1),  # timeout muy corto
    )

    oww = MagicMock(spec=OWWClient)
    oww.is_connected = False

    audio = _make_audio_mock(silent=True)
    gateway = _gateway_mock_with_events([])
    playback = _make_playback_mock()

    received: list[VoiceEvent] = []
    stop_event = asyncio.Event()

    async def _collector() -> None:
        async for ev in bus.subscribe():
            received.append(ev)
            if ev.type == "error":
                stop_event.set()

    collector_task = asyncio.create_task(_collector())

    from domain.state_machine import run as sm_run

    sm_task = asyncio.create_task(sm_run(cfg, bus, audio, gateway, playback))

    # No publicamos ningún wake_word_detected — el test verifica que
    # state_machine publica error tras idle_detection_timeout_s.

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        pass

    sm_task.cancel()
    try:
        await sm_task
    except asyncio.CancelledError:
        pass

    bus.close()
    try:
        await collector_task
    except Exception:
        pass

    error_evs = [e for e in received if e.type == "error"]
    assert len(error_evs) >= 1, (
        f"Esperaba ≥1 evento error por timeout IDLE, recibí {len(error_evs)}\n"
        f"Eventos: {[e.type for e in received]}"
    )
    print(f"  OK  error publicado: {error_evs[0].data['message']!r}")
    print("Test IDLE timeout: PASADO")


# ---------------------------------------------------------------------------
# Test 4: cancel durante RECORDING
# ---------------------------------------------------------------------------

async def _run_cancel_recording_test() -> None:
    print("\n=== Test cancel en RECORDING ===")

    bus = EventBus()
    cfg = _make_config()
    audio = _make_audio_mock(silent=False)  # audio con voz → no termina por silencio

    oww = _make_oww_mock()

    # Publica wake_word_detected en el bus (el state_machine lo consume desde ahí).
    wake_publisher_task = _publish_wake_on_idle(bus)

    gateway = MagicMock(spec=GatewayClient)
    gateway.receive = lambda: (x for x in [])  # never used

    playback = _make_playback_mock()

    cancel_event = asyncio.Event()
    received: list[VoiceEvent] = []
    stop_event = asyncio.Event()

    async def _collector() -> None:
        async for ev in bus.subscribe():
            received.append(ev)

    async def _watcher() -> None:
        idle_count = [0]
        async for ev in bus.subscribe():
            if ev.type == "state_changed" and ev.data.get("state") == "idle":
                idle_count[0] += 1
                if idle_count[0] >= 2:
                    stop_event.set()
                    return

    collector_task = asyncio.create_task(_collector())
    watcher_task = asyncio.create_task(_watcher())

    from domain.state_machine import run as sm_run

    recording_started = _wait_for_event(bus, "recording_started")
    sm_task = asyncio.create_task(
        sm_run(cfg, bus, audio, gateway, playback, cancel_event)
    )

    # Disparar cancel en cuanto empiece RECORDING (sin sondeo, #76)
    async def _fire_cancel() -> None:
        await recording_started
        cancel_event.set()

    fire_task = asyncio.create_task(_fire_cancel())

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        pass

    sm_task.cancel()
    watcher_task.cancel()
    fire_task.cancel()
    wake_publisher_task.cancel()
    recording_started.cancel()
    for t in [sm_task, watcher_task, fire_task, wake_publisher_task, recording_started]:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    bus.close()
    try:
        await collector_task
    except Exception:
        pass

    event_types = [e.type for e in received]
    print(f"\nEventos: {event_types}")

    # recording_ended NO debe aparecer (el turn fue cancelado)
    assert "recording_ended" not in event_types, (
        f"recording_ended no debe publicarse en cancel: {event_types}"
    )
    # send_cancel debe haber sido llamado
    assert gateway.send_cancel.await_count >= 1, "gateway.send_cancel() no fue llamado"
    # send_end NO debe haber sido llamado
    assert gateway.send_end.await_count == 0, "gateway.send_end() no debe llamarse en cancel"
    # vuelve a IDLE (≥2 state_changed idle)
    idle_evs = [e for e in received if e.type == "state_changed" and e.data.get("state") == "idle"]
    assert len(idle_evs) >= 2, f"Esperaba ≥2 idle, got {len(idle_evs)}: {event_types}"
    # el menubar debe enterarse de que el turn se canceló (sin esto, la
    # cancelación es invisible: no hay error, no hay listening — nada).
    assert "cancelled" in event_types, (
        f"Esperaba evento 'cancelled' publicado al bus tras _TurnCancelled: {event_types}"
    )

    print("  OK  recording_ended no publicado")
    print("  OK  send_cancel() llamado")
    print("  OK  send_end() no llamado")
    print("  OK  vuelve a IDLE")
    print("  OK  evento 'cancelled' publicado")
    print("Test cancel en RECORDING: PASADO")


# ---------------------------------------------------------------------------
# Test 5: cancel durante RESPONDING
# ---------------------------------------------------------------------------

async def _run_cancel_responding_test() -> None:
    print("\n=== Test cancel en RESPONDING ===")

    bus = EventBus()
    cfg = _make_config()
    audio = _make_audio_mock(silent=True)

    oww = _make_oww_mock()

    # Publica wake_word_detected en el bus (el state_machine lo consume desde ahí).
    wake_publisher_task = _publish_wake_on_idle(bus)

    # Gateway que emite transcription y luego bloquea (tts lento)
    received_events: list[VoiceEvent] = []

    async def _slow_receive():
        yield GatewayEvent(type="transcription", data={"text": "¿qué hora es?"})
        yield GatewayEvent(type="llm_token", data={"content": "Son "})
        await asyncio.Event().wait()  # bloquea — simulando TTS lento

    gateway = MagicMock(spec=GatewayClient)
    gateway.receive = _slow_receive

    playback = _make_playback_mock()
    cancel_event = asyncio.Event()
    received: list[VoiceEvent] = []  # noqa: F841 — usado por _watcher y _fire_cancel
    stop_event = asyncio.Event()

    async def _collector() -> None:
        async for ev in bus.subscribe():
            received.append(ev)

    async def _watcher() -> None:
        idle_count = [0]
        async for ev in bus.subscribe():
            if ev.type == "state_changed" and ev.data.get("state") == "idle":
                idle_count[0] += 1
                if idle_count[0] >= 2:
                    stop_event.set()
                    return

    collector_task = asyncio.create_task(_collector())
    watcher_task = asyncio.create_task(_watcher())

    from domain.state_machine import run as sm_run

    llm_token_seen = _wait_for_event(bus, "llm_token")
    sm_task = asyncio.create_task(
        sm_run(cfg, bus, audio, gateway, playback, cancel_event)
    )

    # Disparar cancel cuando llegue llm_token (estamos en RESPONDING)
    async def _fire_cancel() -> None:
        await llm_token_seen
        cancel_event.set()

    fire_task = asyncio.create_task(_fire_cancel())

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        pass

    sm_task.cancel()
    watcher_task.cancel()
    fire_task.cancel()
    wake_publisher_task.cancel()
    llm_token_seen.cancel()
    for t in [sm_task, watcher_task, fire_task, wake_publisher_task, llm_token_seen]:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    bus.close()
    try:
        await collector_task
    except Exception:
        pass

    event_types = [e.type for e in received]
    print(f"\nEventos: {event_types}")

    # playback_ended NO debe aparecer
    assert "playback_ended" not in event_types, (
        f"playback_ended no debe publicarse en cancel: {event_types}"
    )
    # send_cancel debe haber sido llamado
    assert gateway.send_cancel.await_count >= 1, "gateway.send_cancel() no fue llamado"
    # vuelve a IDLE
    idle_evs = [e for e in received if e.type == "state_changed" and e.data.get("state") == "idle"]
    assert len(idle_evs) >= 2, f"Esperaba ≥2 idle, got {len(idle_evs)}"
    # el menubar debe enterarse de que el turn se canceló.
    assert "cancelled" in event_types, (
        f"Esperaba evento 'cancelled' publicado al bus tras _TurnCancelled: {event_types}"
    )

    print("  OK  playback_ended no publicado")
    print("  OK  evento 'cancelled' publicado")
    print("  OK  send_cancel() llamado")
    print("  OK  vuelve a IDLE")
    print("Test cancel en RESPONDING: PASADO")


# ---------------------------------------------------------------------------
# Test 6: turn_end cierra el turno en un margen corto, no en 30s
# ---------------------------------------------------------------------------

async def _run_turn_end_grace_period_test() -> None:
    """Reproduce el bug real de producción: el gateway jota-gateway señala
    fin de turno con 'turn_end' (protocolo documentado en
    jota-gateway/docs/client-protocol.md), nunca con 'done'. La conexión WS
    real no se cierra tras el turno — sigue abierta por si hay más turnos.
    Antes del fix, _receive_loop() solo reconocía 'done' para cerrar, así
    que dependía siempre del timeout externo de 30s para cortar.
    """
    print("\n=== Test turn_end: cierre en margen corto, no timeout 30s ===")

    bus = EventBus()
    # recording_timeout_s corto: el mock de audio drena toda la cola en IDLE
    # (ver _idle()), así que RECORDING siempre cierra por timeout absoluto,
    # no por silencio. Lo dejamos corto para que el test quede casi todo el
    # margen para lo que realmente prueba: el cierre en RESPONDING.
    # turn_end_grace_s corto (issue #65: antes 2 s fijos en el módulo).
    cfg = Config(
        gateway=GatewayConfig(
            host="127.0.0.1", client_key="test", connect_timeout_s=5.0, turn_end_grace_s=0.05,
        ),
        device=DeviceConfig(id="test-device"),
        audio=AudioConfig(sample_rate=16000, frames_per_buffer=512, recording_timeout_s=0.2),
        oww=OWWConfig(),
    )
    audio = _make_audio_mock(silent=True)

    async def _receive_then_hang():
        yield GatewayEvent(type="transcription", data={"text": "hola"})
        # Revisión Fase R: solo termina el turn_end del turno abierto tras
        # nuestro send (su turn_start), como hace el gateway real (§5).
        yield GatewayEvent(type="turn_start", data={"turn_id": "t-1", "turn_seq": 1})
        yield GatewayEvent(type="tts_chunk", data={"audio": b"\x00\x01" * 100})
        yield GatewayEvent(type="turn_end", data={"turn_id": "t-1"})
        await asyncio.Event().wait()  # el WS real nunca cierra tras el turno

    gateway = MagicMock(spec=GatewayClient)
    gateway.receive = _receive_then_hang

    playback = _make_playback_mock()

    wake_publisher_task = _publish_wake_on_idle(bus)

    received: list[VoiceEvent] = []
    stop_event = asyncio.Event()

    async def _collector() -> None:
        async for ev in bus.subscribe():
            received.append(ev)
            if ev.type in ("playback_ended", "error"):
                stop_event.set()

    collector_task = asyncio.create_task(_collector())

    from domain.state_machine import run as sm_run

    sm_task = asyncio.create_task(sm_run(cfg, bus, audio, gateway, playback))

    start = asyncio.get_running_loop().time()
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=8.0)
    except asyncio.TimeoutError:
        pass
    elapsed = asyncio.get_running_loop().time() - start

    sm_task.cancel()
    wake_publisher_task.cancel()
    for t in (sm_task, wake_publisher_task):
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    bus.close()
    try:
        await collector_task
    except Exception:
        pass

    event_types = [e.type for e in received]
    print(f"\nEventos tras {elapsed:.1f}s: {event_types}")

    assert "playback_ended" in event_types, (
        f"turn_end debería cerrar el turno en un margen corto (no el timeout "
        f"de 30s). Tras {elapsed:.1f}s, eventos: {event_types}"
    )
    error_evs = [e for e in received if e.type == "error"]
    assert not error_evs, f"No debería haber timeout/error tras turn_end: {error_evs}"
    assert elapsed < 8.0, f"Tardó demasiado en cerrar tras turn_end: {elapsed:.1f}s"
    print("  OK  playback_ended tras un margen corto, sin error de timeout")
    print("Test turn_end grace period: PASADO")


# ---------------------------------------------------------------------------
# Test: RECORDING no debe consumir como "voz del usuario" el backlog
# acumulado en get_queue() durante la espera de _idle() (regresión del
# fix de fan-out de audio, issue #9).
# ---------------------------------------------------------------------------

def _silence_frame_f32() -> bytes:
    import numpy as np
    return np.zeros(512, dtype=np.float32).tobytes()


def _tone_frame_f32(amplitude: float = 0.5) -> bytes:
    import numpy as np
    t = np.arange(512) / 16000.0
    wave = (np.sin(2 * np.pi * 440.0 * t) * amplitude).astype(np.float32)
    return wave.tobytes()


async def _run_recording_discards_stale_idle_backlog_test() -> None:
    """Antes del fan-out de audio (#9), OWWClient._send_audio_loop drenaba
    continuamente get_queue() (la misma cola compartida) durante TODA la
    espera de _idle() a la wake word — como efecto secundario de su propio
    bug, get_queue() nunca acumulaba backlog. Tras separar las colas
    (get_queue() para RECORDING, get_oww_queue() para OWW), nada consume
    get_queue() mientras _idle() espera — si RECORDING no descarta ese
    backlog antes de escuchar, _capture_loop consume el ruido de ambiente
    acumulado como si fuera la voz del usuario, agota
    silence_frames_needed con ese backlog, y corta el turno por silencio
    ANTES de llegar al audio real (pérdida total de la locución del
    usuario, en cualquier turno con más de ~100ms entre wake word y el
    inicio de la frase)."""
    print("\n=== Test regresión: backlog de IDLE no se cuela en RECORDING ===")

    bus = EventBus()
    cfg = _make_config()  # silence_timeout_s=0.1s → 3 frames a 16kHz/512

    audio = SounddeviceBackend(cfg.audio)
    audio._loop = asyncio.get_running_loop()
    audio._capture_q = asyncio.Queue()
    audio._oww_q = asyncio.Queue()

    # A diferencia de _run_cancel_recording_test, aquí RECORDING termina por
    # silencio (no por cancelación) y SÍ llega a RESPONDING — necesita un
    # gateway.receive() real (async generator), no el lambda sync que usan
    # los tests que nunca salen de RECORDING.
    gateway = _gateway_mock_with_events([])

    playback = _make_playback_mock()

    tone = _tone_frame_f32()
    n_backlog = 30

    # Sin esperas reales (#76/#112): en cuanto la máquina entra en IDLE se
    # acumula backlog de ambiente en get_queue() (nadie la consume en IDLE
    # tras el fan-out) y se publica la wake word; en cuanto empieza RECORDING
    # llega la locución real. _on_frame encola vía call_soon_threadsafe: el
    # backlog queda en la cola antes de que IDLE vea la wake word, y la voz
    # llega después del drain síncrono que sigue a recording_started.
    idle_events = bus.subscribe()
    recording_started = _wait_for_event(bus, "recording_started")

    async def _publish_wake_word_after_backlog() -> None:
        async with contextlib.aclosing(idle_events):
            async for ev in idle_events:
                if ev.type == "state_changed" and ev.data.get("state") == "idle":
                    for _ in range(n_backlog):
                        audio._on_frame(_silence_frame_f32())
                    bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))
                    return

    async def _live_speech_producer() -> None:
        await recording_started
        for _ in range(5):
            audio._on_frame(tone)
        for _ in range(5):
            audio._on_frame(_silence_frame_f32())

    wake_task = asyncio.create_task(_publish_wake_word_after_backlog())
    backlog_task = recording_started
    speech_task = asyncio.create_task(_live_speech_producer())

    stop_event = asyncio.Event()
    idle_count = [0]

    async def _watcher() -> None:
        async for ev in bus.subscribe():
            if ev.type == "state_changed" and ev.data.get("state") == "idle":
                idle_count[0] += 1
                if idle_count[0] >= 2:
                    stop_event.set()
                    return

    watcher_task = asyncio.create_task(_watcher())

    from domain.state_machine import run as sm_run
    sm_task = asyncio.create_task(sm_run(cfg, bus, audio, gateway, playback))

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        pass

    tasks = (sm_task, watcher_task, wake_task, backlog_task, speech_task)
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass

    sent_frames = [c.args[0] for c in gateway.send_audio.call_args_list]
    # #112: no basta con que llegue UN tono (un drenaje parcial del backlog
    # también lo cumpliría): deben llegar los 5 y ningún frame del backlog
    # antes del primero.
    assert sent_frames.count(tone) == 5, (
        "RECORDING cortó por silencio consumiendo el backlog de IDLE — "
        f"no procesó toda la locución del usuario "
        f"(tonos enviados: {sent_frames.count(tone)}/5, frames: {len(sent_frames)})"
    )
    # El pre-roll (contexto previo a la wake word, un único envío más largo)
    # es legítimo; de los frames de captura, el primero debe ser ya la voz.
    captured = [f for f in sent_frames if len(f) == len(tone)]
    assert captured.index(tone) == 0, (
        f"se enviaron {captured.index(tone)} frames del backlog de IDLE antes de la voz"
    )
    # Fan-out (#9): la cola de OWW recibió el mismo audio, incluido el backlog.
    assert audio.get_oww_queue().qsize() >= n_backlog + 10
    print("  OK  audio en vivo consumido, backlog de IDLE descartado correctamente")


def test_recording_discards_stale_idle_backlog() -> None:
    asyncio.run(_run_recording_discards_stale_idle_backlog_test())


# ---------------------------------------------------------------------------
# Entry points (pytest + ejecución directa)
# ---------------------------------------------------------------------------

def test_state_machine_e2e() -> None:
    """Compatible con pytest."""
    asyncio.run(_run_e2e_test())


def test_state_machine_error() -> None:
    """Compatible con pytest."""
    asyncio.run(_run_error_test())


def test_state_machine_idle_timeout() -> None:
    """Compatible con pytest."""
    asyncio.run(_run_idle_timeout_test())


def test_state_machine_notification_failure_does_not_block_send_end() -> None:
    asyncio.run(_run_notification_failure_test())


def test_state_machine_cancel_recording() -> None:
    asyncio.run(_run_cancel_recording_test())


def test_state_machine_cancel_responding() -> None:
    asyncio.run(_run_cancel_responding_test())


def test_state_machine_turn_end_grace_period() -> None:
    asyncio.run(_run_turn_end_grace_period_test())


# ---------------------------------------------------------------------------
# Issue #16: cierre anómalo de la conexión a mitad de turno (servidor caído,
# 1006/1009/1011) debe ser visible — publica VoiceEvent(type='error') y NO
# publica playback_ended como si todo hubiera ido bien.
#
# El regression test complementario (turn_end limpio → playback_ended) ya
# existe como _run_turn_end_grace_period_test arriba.
# ---------------------------------------------------------------------------


async def _run_anomalous_close_during_tts_publishes_error_test() -> None:
    """Reproduce el escenario de fallo documentado en la issue #16: el
    gateway cae a mitad de reproducir un tts_chunk. Antes del fix, el
    `except ConnectionClosed` genérico de gateway_client.receive() tragaba
    la ConnectionClosedError, el generador terminaba en silencio igual que
    tras un turn_end normal, y state_machine publicaba playback_ended "todo
    bien". El usuario oía la respuesta cortarse en seco sin saber qué pasó.

    Tras el fix, la ConnectionClosedError debe propagarse desde receive(),
    receive_task.exception() la recoge, run() la maneja publicando
    VoiceEvent(type='error') y NO publica playback_ended (no es un fin de
    turno legítimo).
    """
    print("\n=== Test #16: cierre anómalo a mitad de turno → error, NO playback_ended ===")

    from websockets.exceptions import ConnectionClosedError

    bus = EventBus()
    cfg = _make_config()
    audio = _make_audio_mock(silent=True)

    wake_publisher_task = _publish_wake_on_idle(bus)

    # Gateway que emite transcription + un tts_chunk y luego CAE con
    # ConnectionClosedError — el escenario exacto del bug: el servidor se
    # corta a mitad de un tts_chunk grande.
    # Excepción real de websockets (sin close frame = 1006). Ahora
    # GatewayClient.receive() la traduce a GatewayDisconnected; aquí el mock
    # la lanza tal cual para fijar que cualquier excepción del lector
    # termina el turno con error.
    _drop_exc = ConnectionClosedError(None, None)

    async def _receive_then_drop():
        yield GatewayEvent(type="transcription", data={"text": "hola"})
        yield GatewayEvent(type="tts_chunk", data={"audio": b"\x00\x01" * 100})
        raise _drop_exc

    gateway = MagicMock(spec=GatewayClient)
    gateway.receive = _receive_then_drop

    playback = _make_playback_mock()

    received: list[VoiceEvent] = []
    stop_event = asyncio.Event()
    idle_count = [0]

    async def _collector() -> None:
        async for ev in bus.subscribe():
            received.append(ev)
            if ev.type == "state_changed" and ev.data.get("state") == "idle":
                idle_count[0] += 1
                if idle_count[0] >= 2:
                    stop_event.set()

    collector_task = asyncio.create_task(_collector())

    from domain.state_machine import run as sm_run

    sm_task = asyncio.create_task(sm_run(cfg, bus, audio, gateway, playback))

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        pass

    sm_task.cancel()
    wake_publisher_task.cancel()
    for t in (sm_task, wake_publisher_task):
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    bus.close()
    try:
        await collector_task
    except Exception:
        pass

    event_types = [e.type for e in received]
    print(f"\nEventos: {event_types}")

    # El error del cierre anómalo debe quedar visible Y corresponder al
    # ConnectionClosedError inyectado — sin matchear el mensaje concreto,
    # un fallo espurio no relacionado (p.ej. un fallo de playback.play_chunk)
    # satisfaría la aserción y enmascararía una regresión del fix.
    error_evs = [e for e in received if e.type == "error"]
    assert len(error_evs) >= 1, (
        f"Cierre anómalo a mitad de turno sin visibilidad: esperaba ≥1 "
        f"evento 'error', recibí {len(error_evs)}. Eventos: {event_types}"
    )
    assert any("1006" in e.data["message"] or "no close frame" in e.data["message"]
               for e in error_evs), (
        f"Ningún evento 'error' refleja el cierre anómalo inyectado: "
        f"{[e.data['message'] for e in error_evs]}"
    )
    # Y NO debe publicarse playback_ended (eso era exactamente el bug: el
    # cierre anómalo se hacía pasar por un fin de turno legítimo).
    assert "playback_ended" not in event_types, (
        f"playback_ended NO debe publicarse tras cierre anómalo "
        f"(eso era el bug original): {event_types}"
    )
    # Pero la app debe volver a IDLE para estar lista para el siguiente turno.
    idle_evs = [e for e in received if e.type == "state_changed" and e.data.get("state") == "idle"]
    assert len(idle_evs) >= 2, f"Volver a IDLE tras error: {event_types}"

    print("  OK  error publicado tras cierre anómalo")
    print("  OK  playback_ended NO publicado (no es fin de turno legítimo)")
    print("  OK  vuelta a IDLE tras el error")
    print("Test #16 cierre anómalo: PASADO")


def test_state_machine_anomalous_close_during_tts_publishes_error() -> None:
    asyncio.run(_run_anomalous_close_during_tts_publishes_error_test())


# ---------------------------------------------------------------------------
# Issue #18: mensajes `status` y `error` del gateway durante un turno activo
# deben tener manejo explícito en state_machine._receive_loop.
#
# Antes del fix, esos eventos caían en el else final del dispatcher
# (`log.debug("evento desconocido de gateway: %r", gw_event.type)`) y se
# quedaban invisibles en producción. El orquestador caído a mitad de un
# turno publicaba `{"type":"error","code":"TURN_ERROR","message":"...",
# "fatal":false}` y dejaba de mandar más eventos; el cliente ignoraba ese
# mensaje y esperaba los 30s del timeout externo para reportar el genérico
# "Timeout en estado RESPONDING" — perdiendo el code/message específico
# que el servidor ya había dado, y malgastando ~30s de espera evitable.
# ---------------------------------------------------------------------------


async def _run_gateway_status_event_publishes_to_bus_test() -> None:
    """status es informativo: el gateway puede publicar un `status` mid-turno
    (p.ej. "TTS degradado, fallback engine") sin matar el turno — debe ser
    visible al bus como VoiceEvent(type='gateway_status'), NO solo logueado
    en DEBUG como "evento desconocido".
    """
    print("\n=== Test #18: status del gateway → VoiceEvent(gateway_status) ===")

    bus = EventBus()
    # recording_timeout_s corto: el mock de audio drena toda la cola en IDLE
    # y nunca produce frames nuevos, así que RECORDING termina por timeout
    # absoluto (no por silencio). Acortarlo deja casi todo el margen para lo
    # que realmente prueba el test: visibilidad del status mid-turn.
    cfg = Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=5.0),
        device=DeviceConfig(id="test-device"),
        audio=AudioConfig(sample_rate=16000, frames_per_buffer=512, recording_timeout_s=0.2),
        oww=OWWConfig(),
    )
    audio = _make_audio_mock(silent=True)

    wake_publisher_task = _publish_wake_on_idle(bus)

    gw_events = [
        GatewayEvent(type="transcription", data={"text": "hola"}),
        GatewayEvent(type="status", data={
            "component": "tts",
            "level": "degraded",
            "message": "fallback engine",
        }),
        GatewayEvent(type="tts_chunk", data={"audio": b"\x00\x01" * 100}),
        GatewayEvent(type="turn_end", data={"turn_id": "t-1"}),
    ]
    gateway = _gateway_mock_with_events(gw_events)
    playback = _make_playback_mock()

    received: list[VoiceEvent] = []
    stop_event = asyncio.Event()

    async def _collector() -> None:
        async for ev in bus.subscribe():
            received.append(ev)
            if ev.type == "playback_ended":
                stop_event.set()

    collector_task = asyncio.create_task(_collector())

    from domain.state_machine import run as sm_run
    sm_task = asyncio.create_task(sm_run(cfg, bus, audio, gateway, playback))

    start = asyncio.get_running_loop().time()
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        pass
    elapsed = asyncio.get_running_loop().time() - start

    sm_task.cancel()
    wake_publisher_task.cancel()
    for t in (sm_task, wake_publisher_task):
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    bus.close()
    try:
        await collector_task
    except Exception:
        pass

    event_types = [e.type for e in received]
    print(f"\nEventos tras {elapsed:.1f}s: {event_types}")

    status_evs = [e for e in received if e.type == "gateway_status"]
    assert len(status_evs) >= 1, (
        f"gateway_status no publicado tras un 'status' del gateway "
        f"(bug #18: caía en else → solo log.debug invisible): {event_types}"
    )
    assert status_evs[0].data.get("component") == "tts", (
        f"Status data no propagado al bus: {status_evs[0].data}"
    )
    assert status_evs[0].data.get("level") == "degraded", (
        f"Status level no propagado: {status_evs[0].data}"
    )
    # status es informativo — el turno debe completarse con éxito
    assert "playback_ended" in event_types, (
        f"status informativo no debe terminar el turno: {event_types}"
    )
    error_evs = [e for e in received if e.type == "error"]
    assert not error_evs, f"status informativo no debe generar error: {error_evs}"

    print("  OK  gateway_status publicado al bus con data completo")
    print("  OK  turno continúa hasta playback_ended")
    print("  OK  sin error (status es informativo)")
    print("Test #18 status: PASADO")


async def _run_gateway_error_event_terminates_immediately_test() -> None:
    """Error mid-turno del gateway (orquestador caído, code=TURN_ERROR,
    fatal=false) debe terminar el turno AHORA con el código/mensaje
    específico del servidor — NO esperar al timeout externo de 30s para
    reportar el genérico 'Timeout en estado RESPONDING'.
    """
    print("\n=== Test #18: error mid-turno del gateway → error inmediato con code/message/fatal ===")

    bus = EventBus()
    # recording_timeout_s corto por la misma razón que en el test de status:
    # el audio mock no produce frames nuevos tras el drain de IDLE, así que
    # RECORDING corre hasta el timeout absoluto si no lo acortamos.
    cfg = Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=5.0),
        device=DeviceConfig(id="test-device"),
        audio=AudioConfig(sample_rate=16000, frames_per_buffer=512, recording_timeout_s=0.2),
        oww=OWWConfig(),
    )
    audio = _make_audio_mock(silent=True)

    wake_publisher_task = _publish_wake_on_idle(bus)

    # Escenario exacto de la issue: transcribe → orchestrator cae mid-turn →
    # error con code/message/fatal → no llegan más eventos (el mock termina
    # tras la lista, simulando que el WS se queda mudo por el orquestador
    # caído). `fatal` viaja como STRING "false" (no bool) para ejercer el
    # mismo coercion bug: bool("false") == True en Python, y sin la guarda
    # isinstance(fatal, bool) el fatal real se publicaría como True.
    gw_events = [
        GatewayEvent(type="transcription", data={"text": "hola"}),
        GatewayEvent(type="error", data={
            "code": "TURN_ERROR",
            "message": "orchestrator unavailable",
            "fatal": "false",
        }),
    ]
    gateway = _gateway_mock_with_events(gw_events)
    playback = _make_playback_mock()

    received: list[VoiceEvent] = []
    stop_event = asyncio.Event()

    async def _collector() -> None:
        async for ev in bus.subscribe():
            received.append(ev)
            # Esperar al SEGUNDO state_changed(idle): el primero es el idle
            # inicial previo al turno; el segundo es el que demuestra que
            # state_machine volvió a IDLE tras el error del gateway.
            if (
                ev.type == "state_changed"
                and ev.data.get("state") == "idle"
                and sum(
                    1 for e in received
                    if e.type == "state_changed" and e.data.get("state") == "idle"
                ) >= 2
            ):
                stop_event.set()

    collector_task = asyncio.create_task(_collector())

    from domain.state_machine import run as sm_run
    sm_task = asyncio.create_task(sm_run(cfg, bus, audio, gateway, playback))

    start = asyncio.get_running_loop().time()
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        pass
    elapsed = asyncio.get_running_loop().time() - start

    sm_task.cancel()
    wake_publisher_task.cancel()
    for t in (sm_task, wake_publisher_task):
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    bus.close()
    try:
        await collector_task
    except Exception:
        pass

    event_types = [e.type for e in received]
    print(f"\nEventos tras {elapsed:.1f}s: {event_types}")

    error_evs = [e for e in received if e.type == "error"]
    assert len(error_evs) >= 1, (
        f"error mid-turno del gateway sin visibilidad: {event_types}"
    )
    # code/message/fatal del gateway deben propagarse al bus, no quedar
    # envueltos en el str de un timeout genérico.
    err = error_evs[0]
    assert err.data.get("code") == "TURN_ERROR", (
        f"code del gateway debe propagarse al bus: {err.data}"
    )
    assert err.data.get("message") == "orchestrator unavailable", (
        f"message del gateway debe propagarse tal cual: {err.data}"
    )
    assert err.data.get("fatal") is False, (
        f"fatal debe propagarse al bus: {err.data}"
    )
    assert "Timeout en estado RESPONDING" not in str(err.data), (
        f"NO debe aparecer el mensaje genérico del timeout 30s: {err.data}"
    )
    # El turno debe haber terminado rápidamente, NO esperar 30s del timeout
    # externo (que era el bug original de la issue #18).
    assert elapsed < 5.0, (
        f"El error del gateway debe propagarse inmediato, no esperar el "
        f"timeout externo de 30s: tardó {elapsed:.1f}s"
    )
    # playback_ended NO debe publicarse (no es fin de turno legítimo)
    assert "playback_ended" not in event_types, (
        f"playback_ended NO debe publicarse tras error mid-turno: {event_types}"
    )
    # playback.drain() NO debe llamarse — termina con error, no con éxito
    assert playback.drain.await_count == 0, (
        f"playback.drain() NO debe llamarse tras error mid-turno: {event_types}"
    )
    # Debe haber al menos 2 idle: el inicial previo al turno y el recovery
    # tras el error del gateway. Más fuerte que un any() suelto, que sería
    # satisfecho por el idle inicial sin probar la recuperación.
    idle_evs = [e for e in received if e.type == "state_changed"
                and e.data.get("state") == "idle"]
    assert len(idle_evs) >= 2, (
        f"Debe volver a IDLE tras el error del gateway (esperaba ≥2 idle, "
        f"recibí {len(idle_evs)}): {event_types}"
    )
    # Y ese segundo idle debe ocurrir DESPUÉS del error — no solo existir,
    # sino ser respuesta al fallo.
    error_idx = next(i for i, e in enumerate(received) if e.type == "error")
    idle_after_error = next(
        (i for i, e in enumerate(received)
         if i > error_idx and e.type == "state_changed"
         and e.data.get("state") == "idle"),
        None,
    )
    assert idle_after_error is not None, (
        f"Debe haber un state_changed(idle) DESPUÉS del evento error "
        f"(recovered to IDLE tras el fallo del gateway): {event_types}"
    )

    print(f"  OK  error publicado tras {elapsed:.2f}s con code=TURN_ERROR / fatal=False")
    print("  OK  mensaje del gateway propagado, NO 'Timeout en estado RESPONDING'")
    print("  OK  playback_ended NO publicado (no es fin de turno legítimo)")
    print("  OK  playback.drain() NO llamado")
    print("  OK  vuelta a IDLE (segundo idle, posterior al error)")
    print("Test #18 error: PASADO")


def test_state_machine_gateway_status_event_publishes_to_bus() -> None:
    asyncio.run(_run_gateway_status_event_publishes_to_bus_test())


def test_state_machine_gateway_error_event_terminates_immediately() -> None:
    asyncio.run(_run_gateway_error_event_terminates_immediately_test())


# ---------------------------------------------------------------------------
# Issue #24: persistent_session — modo persistente (1 connect + N disconnects
# nunca) vs modo efímero (connect/disconnect por turno)
# ---------------------------------------------------------------------------

from domain.state_machine import run as sm_run


async def _make_async_iter(events_iter):
    """Convierte un iterador sync en async generator."""
    for ev in events_iter:
        yield ev


@pytest.mark.asyncio
async def test_dos_turnos_con_una_sola_connect_persistente():
    """Issue #24: en modo persistente, múltiples turnos usan una sola conexión."""
    from backends.gateway_client import GatewayEvent

    bus = EventBus()
    cfg = _make_config(persistent_session=True)

    gw_events_cycle_1 = [
        GatewayEvent(type="turn_end", data={"turn_id": "t-1"}),
    ]
    gw_events_cycle_2 = [
        GatewayEvent(type="turn_end", data={"turn_id": "t-2"}),
    ]

    # El mock debe devolver cycle_1 la primera vez y cycle_2 la segunda.
    cycles = [iter(gw_events_cycle_1), iter(gw_events_cycle_2)]
    cycle_calls = [0]

    def _gateway_mock_multi_cycle():
        gateway = MagicMock(spec=GatewayClient)
        gateway.is_connected = True
        gateway.receive = lambda: _make_async_iter(cycles[cycle_calls[0]])
        cycle_calls[0] += 1
        return gateway

    audio = _make_audio_mock(silent=True)
    playback = _make_playback_mock()

    # Publicar dos wake_word_detected (uno por cada IDLE) para forzar dos ciclos
    wake_task = _publish_wake_on_idle(bus, times=2)

    # Contar ciclos
    idle_count = [0]
    async def _watch_idle():
        async for ev in bus.subscribe():
            if ev.type == "state_changed" and ev.data.get("state") == "idle":
                idle_count[0] += 1
                if idle_count[0] >= 2:
                    return

    watcher = asyncio.create_task(_watch_idle())

    # Para que el segundo ciclo use otro gateway mock con sus propios events
    # es complicado con un solo mock; en su lugar, verificar que el mismo
    # gateway no se desconectó entre turnos.
    gateway = _gateway_mock_multi_cycle()
    sm_task = asyncio.create_task(sm_run(cfg, bus, audio, gateway, playback))

    try:
        await asyncio.wait_for(watcher, timeout=5.0)
    except asyncio.TimeoutError:
        pass

    sm_task.cancel()
    wake_task.cancel()
    try:
        await sm_task
    except asyncio.CancelledError:
        pass
    try:
        await wake_task
    except asyncio.CancelledError:
        pass

    # En modo persistente, connect se llama UNA vez (en main, no en _recording)
    # y disconnect NUNCA (se hace en main.shutdown).
    # Como aquí el state_machine recibe un gateway ya conectado (no llama
    # connect), connect.await_count debe ser 0 y disconnect.await_count debe ser 0.
    assert gateway.connect.await_count == 0, (
        "En modo persistente, _recording NO debe llamar connect() "
        f"(se llamó {gateway.connect.await_count} veces)"
    )
    assert gateway.disconnect.await_count == 0, (
        "En modo persistente, _cleanup NO debe llamar disconnect() "
        f"(se llamó {gateway.disconnect.await_count} veces)"
    )


# test_recording_guard_publica_error_si_ws_muerto fijaba el comportamiento
# roto de #118 (sesión caída → GATEWAY_DISCONNECTED y turno perdido, sin
# reconectar nunca). Sustituido por los tests de reconexión perezosa de
# tests/domain/test_gateway_session.py:
#   test_recording_reconecta_si_la_sesion_esta_caida
#   test_recording_reconexion_fallida_da_error_visible_sin_enviar_audio
#   test_cierre_a_mitad_de_turno_da_error_y_el_siguiente_turno_funciona


@pytest.mark.asyncio
async def test_interrupted_durante_responding_dispara_cancel():
    """Issue #24: 'interrupted' del gateway durante RESPONDING cancela el turno.

    El gateway envía 'interrupted' como primer evento, lo que activa
    cancel_event dentro de _receive_loop, provocando _TurnCancelled y
    la publicación de VoiceEvent('cancelled').
    """
    from backends.gateway_client import GatewayEvent

    bus = EventBus()
    cfg = _make_config(persistent_session=True)

    interrupted_event = GatewayEvent(type="interrupted", data={})

    gateway = MagicMock(spec=GatewayClient)
    gateway.is_connected = True

    # El gateway emite interrupted como primer evento (simula barge-in del servidor).
    async def _receive_with_interrupt():
        yield interrupted_event
        await asyncio.Event().wait()  # luego bloquea

    gateway.receive = _receive_with_interrupt

    audio = _make_audio_mock(silent=True)
    playback = _make_playback_mock()

    # Mismo patrón que _run_cancel_responding_test: wake publisher en task separada.
    wake_publisher_task = _publish_wake_on_idle(bus)

    received: list[VoiceEvent] = []
    stop_event = asyncio.Event()

    async def _collector() -> None:
        async for ev in bus.subscribe():
            received.append(ev)
            if ev.type == "cancelled":
                stop_event.set()

    collector_task = asyncio.create_task(_collector())

    sm_task = asyncio.create_task(sm_run(cfg, bus, audio, gateway, playback))

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        pass

    sm_task.cancel()
    collector_task.cancel()
    wake_publisher_task.cancel()
    for t in [sm_task, collector_task, wake_publisher_task]:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    bus.close()

    cancelled_events = [e for e in received if e.type == "cancelled"]
    assert len(cancelled_events) >= 1, (
        f"Esperaba VoiceEvent('cancelled') tras 'interrupted'; recibí "
        f"{[(e.type, e.data) for e in received if e.type in ('cancelled', 'error')]}"
    )
    gateway.send_cancel.assert_awaited()


@pytest.mark.asyncio
async def test_dos_turnos_conectan_y_desconectan_en_modo_efimero():
    """Issue #24: en modo efímero (persistent_session=false), connect/disconnect se llaman por turno.

    Este test usa la MISMA estructura que el e2e test exitoso para garantizar
    un ciclo completo: transcription + llm_token + tts_chunk. Verifica que
    en modo efímero connect() se llama (no se salta) y disconnect() también.
    La diferencia con el test de modo persistente es que aquí connect() SÍ
    se invoca (mientras que en persistente es main() quien conecta).
    """
    from backends.gateway_client import GatewayEvent

    bus = EventBus()
    cfg = _make_config(persistent_session=False)

    # Un ciclo completo: transcription + llm_token + tts_chunk + turn_end.
    # Esto permite que playback drene correctamente y el ciclo llegue a IDLE.
    gw_events = [
        GatewayEvent(type="transcription", data={"text": "hola"}),
        GatewayEvent(type="llm_token", data={"content": "Hola."}),
        GatewayEvent(type="tts_chunk", data={"audio": b"\x00\x01" * 100}),
        GatewayEvent(type="turn_end", data={"turn_id": "t-1"}),
    ]

    gateway = MagicMock(spec=GatewayClient)
    gateway.is_connected = True
    gateway.receive = lambda: _make_async_iter(gw_events)

    audio = _make_audio_mock(silent=True)
    playback = _make_playback_mock()

    # Publicar wake_word_detected cuando sm_run esté en _idle()
    idle_ready = asyncio.Event()

    async def _wait_idle_then_publish():
        async for ev in bus.subscribe():
            if ev.type == "state_changed" and ev.data.get("state") == "idle":
                idle_ready.set()
                bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))
                return

    publish_task = asyncio.create_task(_wait_idle_then_publish())

    # Esperar al segundo idle (fin del primer ciclo)
    idle_count = [0]
    stop_event = asyncio.Event()

    async def _watch_idle():
        async for ev in bus.subscribe():
            if ev.type == "state_changed" and ev.data.get("state") == "idle":
                idle_count[0] += 1
                if idle_count[0] >= 2:
                    stop_event.set()
                    return

    watcher = asyncio.create_task(_watch_idle())
    sm_task = asyncio.create_task(sm_run(cfg, bus, audio, gateway, playback))

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=8.0)
    except asyncio.TimeoutError:
        pass

    sm_task.cancel()
    publish_task.cancel()
    try:
        await sm_task
    except asyncio.CancelledError:
        pass
    try:
        await publish_task
    except asyncio.CancelledError:
        pass
    await watcher

    # Modo efímero: connect() se llama en _recording y disconnect() en _cleanup.
    # Un ciclo completo: connect == 1, disconnect == 1.
    assert gateway.connect.await_count >= 1, (
        f"Modo efímero debe llamar connect() al menos 1 vez; "
        f"recibí {gateway.connect.await_count}"
    )
    assert gateway.disconnect.await_count >= 1, (
        f"Modo efímero debe llamar disconnect() al menos 1 vez; "
        f"recibí {gateway.disconnect.await_count}"
    )
    # Y NO debe ser 0
    assert gateway.connect.await_count == 1, (
        f"Modo efímero esperaba exactamente 1 connect (un turno); "
        f"recibí {gateway.connect.await_count}"
    )
