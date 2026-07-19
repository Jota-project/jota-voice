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
import sys
import os
import types
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Stubs para dependencias de hardware (pyaudio, websockets, numpy)
# Deben instalarse ANTES de importar los módulos del proyecto.
# ---------------------------------------------------------------------------

def _install_stubs() -> None:
    """Instala módulos stub para pyaudio y websockets si no están disponibles."""
    # pyaudio stub
    if "pyaudio" not in sys.modules:
        try:
            import pyaudio  # noqa: F401
        except ImportError:
            stub = types.ModuleType("pyaudio")
            stub.paInt16 = 8
            stub.paContinue = 0

            class _FakeStream:
                def write(self, data: bytes) -> None: pass
                def stop_stream(self) -> None: pass
                def close(self) -> None: pass

            class _FakePyAudio:
                def open(self, **kwargs): return _FakeStream()
                def terminate(self) -> None: pass

            stub.PyAudio = _FakePyAudio
            stub.Stream = _FakeStream
            sys.modules["pyaudio"] = stub

    # websockets stub (gateway_client lo importa). Añadimos las subclases
    # que necesitamos para distinguir OK vs Error al módulo real de websockets
    # si no están — websockets 16.0+ las tiene ya, así que esto es no-op en
    # práctica; pero dejamos la defensa por si una versión más nueva las mueve.
    def _ensure_exception_subclasses() -> None:
        try:
            from websockets import exceptions as _exc
        except ImportError:
            return

        class _FakeConnectionClosed(Exception):
            """Base laxa — hereda Exception directamente."""

        class _FakeConnectionClosedOK(_FakeConnectionClosed):
            pass

        class _FakeConnectionClosedError(_FakeConnectionClosed):
            pass

        # Sustituimos siempre: websockets real puede tener __init__ estricto
        # incompatible con tests que pasan solo un mensaje.
        _exc.ConnectionClosed = _FakeConnectionClosed
        _exc.ConnectionClosedOK = _FakeConnectionClosedOK
        _exc.ConnectionClosedError = _FakeConnectionClosedError

    _ensure_exception_subclasses()


_install_stubs()

# Importar módulos del proyecto
from domain.event_bus import EventBus, VoiceEvent                       # noqa: E402
from config import Config, GatewayConfig, AudioConfig, OWWConfig, DeviceConfig  # noqa: E402
from backends.gateway_client import GatewayEvent                        # noqa: E402
from backends.audio_sounddevice import SounddeviceBackend               # noqa: E402


# ---------------------------------------------------------------------------
# Helpers de mock
# ---------------------------------------------------------------------------

def _make_config() -> Config:
    return Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=5.0),
        device=DeviceConfig(id="test-device"),
        audio=AudioConfig(
            sample_rate=16000,
            frames_per_buffer=512,
            silence_timeout_s=0.1,   # ~3 frames de silencio con silence_count
            recording_timeout_s=5.0,
        ),
        oww=OWWConfig(),
    )


def _make_audio_mock(*, silent: bool = True) -> MagicMock:
    """
    Mock de AudioCapture.
    - get_queue() devuelve una Queue con 20 frames de audio float32.
    - get_preroll() devuelve bytes vacíos.
    - is_silence() devuelve `silent` para todos los frames.
    """
    import numpy as np

    audio = MagicMock()

    q: asyncio.Queue[bytes] = asyncio.Queue()
    frame = bytes(512 * 4)  # 512 float32 samples = 2048 bytes de ceros
    for _ in range(20):
        q.put_nowait(frame)

    audio.get_queue.return_value = q
    audio.get_preroll.return_value = b""
    audio.is_silence.return_value = silent
    return audio


def _make_oww_mock(wake_word: str = "ok_nabu") -> MagicMock:
    """Mock de OWWClient que detecta el wake word al primer intento."""
    oww = MagicMock()
    oww.is_connected = True
    oww.send_audio = AsyncMock()
    oww.wait_for_detection = AsyncMock(return_value=wake_word)
    oww.connect_with_backoff = AsyncMock()
    oww.disconnect = AsyncMock()
    return oww


def _gateway_mock_with_events(events: list[GatewayEvent]) -> MagicMock:
    """Mock de GatewayClient que emite una lista de GatewayEvents y luego cierra."""

    async def _receive() -> AsyncGenerator[GatewayEvent, None]:
        for ev in events:
            yield ev

    gateway = MagicMock()
    gateway.connect = AsyncMock()
    gateway.disconnect = AsyncMock()
    gateway.send_audio = AsyncMock()
    gateway.send_end = AsyncMock()
    gateway.send_text = AsyncMock()
    gateway.receive = _receive
    return gateway


def _make_playback_mock() -> MagicMock:
    playback = MagicMock()
    playback.push_token = MagicMock()
    playback.play_chunk = AsyncMock()
    playback.play_notification = AsyncMock()
    playback.drain = AsyncMock()
    playback.reset = MagicMock()
    playback.close = MagicMock()
    return playback


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
    call_count = [0]

    async def _publish_wake_words() -> None:
        while True:
            await asyncio.sleep(0)
            if call_count[0] == 0:
                call_count[0] += 1
                bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))

    wake_publisher_task = asyncio.create_task(_publish_wake_words())

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
    cfg = _make_config()
    audio = _make_audio_mock(silent=True)

    oww = _make_oww_mock()

    # Publica wake_word_detected en el bus (el state_machine lo consume desde ahí).
    call_count = [0]

    async def _publish_wake_words() -> None:
        while True:
            await asyncio.sleep(0)
            if call_count[0] == 0:
                call_count[0] += 1
                bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))

    wake_publisher_task = asyncio.create_task(_publish_wake_words())

    gateway = MagicMock()
    gateway.connect = AsyncMock(side_effect=ConnectionRefusedError("gateway no disponible"))
    gateway.disconnect = AsyncMock()
    gateway.send_audio = AsyncMock()
    gateway.send_end = AsyncMock()
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

    call_count = [0]

    async def _publish_wake_words() -> None:
        while True:
            await asyncio.sleep(0)
            if call_count[0] == 0:
                call_count[0] += 1
                bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))

    wake_publisher_task = asyncio.create_task(_publish_wake_words())

    gateway = _gateway_mock_with_events([])  # RESPONDING termina rápido (sin eventos)

    playback = _make_playback_mock()
    playback.play_notification = AsyncMock(
        side_effect=RuntimeError("Error opening OutputStream: Internal PortAudio error [PaErrorCode -9986]")
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
    gateway.send_audio = AsyncMock(
        side_effect=[None, None, ConnectionError("corte de red")]
    )

    playback = _make_playback_mock()

    call_count = [0]

    async def _publish_wake_words() -> None:
        while True:
            await asyncio.sleep(0)
            if call_count[0] == 0:
                call_count[0] += 1
                bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))

    wake_publisher_task = asyncio.create_task(_publish_wake_words())

    # Producer que introduce frames no silenciosos a la cola tras el drain
    # inicial de _recording() — sin esto, _capture_loop espera eternamente y
    # nunca llega a invocar gateway.send_audio(). El test no reproduciría
    # el escenario real de corte de red a mitad de grabación.
    async def _audio_producer() -> None:
        await asyncio.sleep(0.1)  # deja pasar el drain de _recording()
        for _ in range(20):
            audio._on_frame(_tone_frame_f32())
            await asyncio.sleep(0.01)

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

    oww = MagicMock()
    oww.is_connected = False
    oww.connect_with_backoff = AsyncMock()
    oww.disconnect = AsyncMock()
    oww.send_audio = AsyncMock()

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
    call_count = [0]

    async def _publish_wake_words() -> None:
        while True:
            await asyncio.sleep(0)
            if call_count[0] == 0:
                call_count[0] += 1
                bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))

    wake_publisher_task = asyncio.create_task(_publish_wake_words())

    gateway = MagicMock()
    gateway.connect = AsyncMock()
    gateway.disconnect = AsyncMock()
    gateway.send_audio = AsyncMock()
    gateway.send_end = AsyncMock()
    gateway.send_cancel = AsyncMock()
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

    sm_task = asyncio.create_task(
        sm_run(cfg, bus, audio, gateway, playback, cancel_event)
    )

    # Esperar a que empiece RECORDING y disparar cancel
    async def _fire_cancel() -> None:
        # Esperar recording_started
        while not any(e.type == "recording_started" for e in received):
            await asyncio.sleep(0.01)
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
    for t in [sm_task, watcher_task, fire_task, wake_publisher_task]:
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
    call_count = [0]

    async def _publish_wake_words() -> None:
        while True:
            await asyncio.sleep(0)
            if call_count[0] == 0:
                call_count[0] += 1
                bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))

    wake_publisher_task = asyncio.create_task(_publish_wake_words())

    # Gateway que emite transcription y luego bloquea (tts lento)
    received_events: list[VoiceEvent] = []

    async def _slow_receive():
        yield GatewayEvent(type="transcription", data={"text": "¿qué hora es?"})
        yield GatewayEvent(type="llm_token", data={"content": "Son "})
        await asyncio.Event().wait()  # bloquea — simulando TTS lento

    gateway = MagicMock()
    gateway.connect = AsyncMock()
    gateway.disconnect = AsyncMock()
    gateway.send_audio = AsyncMock()
    gateway.send_end = AsyncMock()
    gateway.send_cancel = AsyncMock()
    gateway.send_text = AsyncMock()
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

    sm_task = asyncio.create_task(
        sm_run(cfg, bus, audio, gateway, playback, cancel_event)
    )

    # Disparar cancel cuando llegue llm_token (estamos en RESPONDING)
    async def _fire_cancel() -> None:
        while not any(e.type == "llm_token" for e in received):
            await asyncio.sleep(0.01)
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
    for t in [sm_task, watcher_task, fire_task, wake_publisher_task]:
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
    cfg = Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=5.0),
        device=DeviceConfig(id="test-device"),
        audio=AudioConfig(sample_rate=16000, frames_per_buffer=512, recording_timeout_s=0.2),
        oww=OWWConfig(),
    )
    audio = _make_audio_mock(silent=True)

    async def _receive_then_hang():
        yield GatewayEvent(type="transcription", data={"text": "hola"})
        yield GatewayEvent(type="tts_chunk", data={"audio": b"\x00\x01" * 100})
        yield GatewayEvent(type="turn_end", data={"turn_id": "t-1"})
        await asyncio.Event().wait()  # el WS real nunca cierra tras el turno

    gateway = MagicMock()
    gateway.connect = AsyncMock()
    gateway.disconnect = AsyncMock()
    gateway.send_audio = AsyncMock()
    gateway.send_end = AsyncMock()
    gateway.send_text = AsyncMock()
    gateway.receive = _receive_then_hang

    playback = _make_playback_mock()

    call_count = [0]

    async def _publish_wake_words() -> None:
        while True:
            await asyncio.sleep(0)
            if call_count[0] == 0:
                call_count[0] += 1
                bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))

    wake_publisher_task = asyncio.create_task(_publish_wake_words())

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

    wake_published = asyncio.Event()
    tone = _tone_frame_f32()

    async def _publish_wake_word_soon() -> None:
        await asyncio.sleep(0.05)  # deja tiempo a que se acumule backlog
        bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))
        wake_published.set()

    async def _ambient_backlog_producer() -> None:
        # Simula la captura continua de mic mientras _idle() espera — nadie
        # consume get_queue() en ese tramo tras el fix de fan-out.
        while not wake_published.is_set():
            audio._on_frame(_silence_frame_f32())
            await asyncio.sleep(0.002)

    async def _live_speech_producer() -> None:
        await wake_published.wait()
        for _ in range(5):
            audio._on_frame(tone)
            await asyncio.sleep(0.01)
        for _ in range(5):
            audio._on_frame(_silence_frame_f32())
            await asyncio.sleep(0.01)

    wake_task = asyncio.create_task(_publish_wake_word_soon())
    backlog_task = asyncio.create_task(_ambient_backlog_producer())
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
    assert tone in sent_frames, (
        "RECORDING cortó por silencio consumiendo solo el backlog de IDLE — "
        f"nunca llegó a procesar el audio en vivo del usuario "
        f"(frames enviados al gateway: {len(sent_frames)})"
    )
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

    call_count = [0]

    async def _publish_wake_words() -> None:
        while True:
            await asyncio.sleep(0)
            if call_count[0] == 0:
                call_count[0] += 1
                bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))

    wake_publisher_task = asyncio.create_task(_publish_wake_words())

    # Gateway que emite transcription + un tts_chunk y luego CAE con
    # ConnectionClosedError — el escenario exacto del bug: el servidor se
    # corta a mitad de un tts_chunk grande.
    _drop_exc = ConnectionClosedError("gateway dropped mid-tts (1006 abnormal closure)")

    async def _receive_then_drop():
        yield GatewayEvent(type="transcription", data={"text": "hola"})
        yield GatewayEvent(type="tts_chunk", data={"audio": b"\x00\x01" * 100})
        raise _drop_exc

    gateway = MagicMock()
    gateway.connect = AsyncMock()
    gateway.disconnect = AsyncMock()
    gateway.send_audio = AsyncMock()
    gateway.send_end = AsyncMock()
    gateway.send_cancel = AsyncMock()
    gateway.send_text = AsyncMock()
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
    assert any("1006" in e.data["message"] or "mid-tts" in e.data["message"]
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


def test_idle_type_hints_resolve() -> None:
    """_idle()/_recording() deben anotar audio con AudioBackend (importado), no
    con el AudioCapture obsoleto que ya no se importa en el módulo."""
    import typing
    from domain import state_machine

    hints = typing.get_type_hints(state_machine._idle)
    assert hints["audio"] is state_machine.AudioBackend

    hints = typing.get_type_hints(state_machine._recording)
    assert hints["audio"] is state_machine.AudioBackend


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

    call_count = [0]

    async def _publish_wake_words() -> None:
        while True:
            await asyncio.sleep(0)
            if call_count[0] == 0:
                call_count[0] += 1
                bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))

    wake_publisher_task = asyncio.create_task(_publish_wake_words())

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

    call_count = [0]

    async def _publish_wake_words() -> None:
        while True:
            await asyncio.sleep(0)
            if call_count[0] == 0:
                call_count[0] += 1
                bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": "ok_nabu"}))

    wake_publisher_task = asyncio.create_task(_publish_wake_words())

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
