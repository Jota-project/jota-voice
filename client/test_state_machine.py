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

    # websockets stub (gateway_client lo importa)
    if "websockets" not in sys.modules:
        try:
            import websockets  # noqa: F401
        except ImportError:
            stub = types.ModuleType("websockets")
            exc_stub = types.ModuleType("websockets.exceptions")

            class ConnectionClosed(Exception):
                pass

            exc_stub.ConnectionClosed = ConnectionClosed
            stub.exceptions = exc_stub
            stub.connect = AsyncMock()
            sys.modules["websockets"] = stub
            sys.modules["websockets.exceptions"] = exc_stub


_install_stubs()

# Añadir client/ al sys.path para importar módulos locales
_here = os.path.dirname(__file__)  # .../client
if _here not in sys.path:
    sys.path.insert(0, _here)

# Importar módulos del proyecto
from event_bus import EventBus, VoiceEvent          # noqa: E402
from config import Config, GatewayConfig, AudioConfig, OWWConfig  # noqa: E402
from gateway_client import GatewayEvent             # noqa: E402


# ---------------------------------------------------------------------------
# Helpers de mock
# ---------------------------------------------------------------------------

def _make_config() -> Config:
    return Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="test", connect_timeout_s=5.0),
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
    gateway.receive = _receive
    return gateway


def _make_playback_mock() -> MagicMock:
    playback = MagicMock()
    playback.push_token = MagicMock()
    playback.play_chunk = AsyncMock()
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

    # Preparar secuencia de detecciones:
    #   - 1er call: devuelve "ok_nabu" inmediatamente
    #   - 2do call: bloquea hasta cancelación (simula espera real en el 2do ciclo)
    call_count = [0]

    async def _wait_once_then_block() -> str:
        call_count[0] += 1
        if call_count[0] == 1:
            return "ok_nabu"
        await asyncio.Event().wait()
        return "ok_nabu"  # never reached

    oww.wait_for_detection = _wait_once_then_block

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

    from state_machine import run as sm_run

    sm_task = asyncio.create_task(sm_run(cfg, bus, audio, oww, gateway, playback))

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        pass

    sm_task.cancel()
    watcher_task.cancel()
    try:
        await sm_task
    except asyncio.CancelledError:
        pass
    try:
        await watcher_task
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

    call_count = [0]

    async def _wait_once_then_block() -> str:
        call_count[0] += 1
        if call_count[0] == 1:
            return "ok_nabu"
        await asyncio.Event().wait()
        return "ok_nabu"

    oww = _make_oww_mock()
    oww.wait_for_detection = _wait_once_then_block

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

    from state_machine import run as sm_run

    sm_task = asyncio.create_task(sm_run(cfg, bus, audio, oww, gateway, playback))

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
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
    assert len(error_evs) >= 1, f"Esperaba ≥1 evento error, recibí {len(error_evs)}"
    assert "gateway no disponible" in error_evs[0].data["message"], (
        f"Mensaje de error incorrecto: {error_evs[0].data['message']!r}"
    )
    print(f"  OK  error publicado: {error_evs[0].data['message']!r}")
    print("Test error: PASADO")


# ---------------------------------------------------------------------------
# Entry points (pytest + ejecución directa)
# ---------------------------------------------------------------------------

async def _run_all() -> None:
    await _run_e2e_test()
    await _run_error_test()
    print("\n=== TODOS LOS TESTS PASARON ===")


def test_state_machine_e2e() -> None:
    """Compatible con pytest."""
    asyncio.run(_run_e2e_test())


def test_state_machine_error() -> None:
    """Compatible con pytest."""
    asyncio.run(_run_error_test())


if __name__ == "__main__":
    asyncio.run(_run_all())
