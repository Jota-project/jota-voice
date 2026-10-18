"""Tests de recording_timeout_s como límite duro — Issue #23.

Replican el bug: el `while loop.time() < deadline` en _capture_loop solo
verifica el deadline al INICIO de cada iteración. Si `gateway.send_audio()`
cuelga más de `recording_timeout_s` (típico bajo red degradada con
WebSocket congestionado), el código se queda dentro del `await
gateway.send_audio(frame)` y nunca respeta el deadline.

Aceptación de la issue: 20 turnos bajo tc netem sin cuelgues. Aquí
probamos la unidad mínima: que el estado RECORDING termina en tiempo
bounded aunque send_audio esté bloqueado.

Simplificación: en vez de tc netem real, mockeamos send_audio con un
await que nunca retorna (asyncio.Event().wait()). La invariante es la
misma: si el deadline no es duro, la corutina se cuelga.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from app.playback_engine import PlaybackEngine
from backends.audio_base import AudioBackend
from backends.gateway_client import GatewayClient
from config import AudioConfig, Config, DeviceConfig, GatewayConfig
from domain.event_bus import EventBus


# ---------------------------------------------------------------------------
# Helpers de mock (mismo patrón que test_state_machine_cancel.py)
# ---------------------------------------------------------------------------

def _cfg(recording_timeout_s: float = 0.3) -> Config:
    return Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="x"),
        device=DeviceConfig(id="test"),
        audio=AudioConfig(
            sample_rate=16000,
            frames_per_buffer=512,
            # silence_timeout_s alto para que el fin de RECORDING venga
            # del deadline, no de la detección de silencio.
            silence_timeout_s=10.0,
            recording_timeout_s=recording_timeout_s,
        ),
    )


def _audio_mock_with_frames(n: int) -> tuple[MagicMock, asyncio.Queue[bytes]]:
    """Devuelve (audio_mock, queue) con la queue VACÍA al entrar en RECORDING.

    _recording drena la cola al entrar (descarta el backlog de IDLE), así
    que los frames se meten justo DESPUÉS del drain: get_preroll() se llama
    tras el drain y antes de _capture_loop, y su side_effect encola los ``n``
    frames. Sin esperas reales (issue #76).
    """
    audio = MagicMock(spec=AudioBackend)
    q: asyncio.Queue[bytes] = asyncio.Queue()
    audio.get_queue.return_value = q
    audio.is_silence.return_value = False  # nunca silencio → solo el deadline termina

    def _preroll_then_frames() -> bytes:
        for _ in range(n):
            q.put_nowait(b"\x00" * 100)
        return b""

    audio.get_preroll.side_effect = _preroll_then_frames
    return audio, q


def _gateway_mock() -> MagicMock:
    return MagicMock(spec=GatewayClient)


def _playback_mock() -> MagicMock:
    return MagicMock(spec=PlaybackEngine)


# ---------------------------------------------------------------------------
# Test crítico (RED sin el fix)
# ---------------------------------------------------------------------------

def test_recording_timeout_is_hard_limit_when_send_audio_blocks() -> None:
    """Issue #23: aunque `gateway.send_audio` se cuelgue > recording_timeout_s,
    el estado RECORDING debe terminar en tiempo bounded.

    Sin el fix (wrap de `send_audio` con wait_for + break), el código se
    queda dentro de `await gateway.send_audio(frame)` indefinidamente. Con
    el fix, el deadline dispara un TimeoutError, _capture_loop rompe el
    bucle, y _recording continúa hasta send_end().
    """

    async def _driver() -> None:
        cfg = _cfg(recording_timeout_s=0.3)
        bus = EventBus()
        audio, _ = _audio_mock_with_frames(n=5)
        gateway = _gateway_mock()
        playback = _playback_mock()
        cancel_event = asyncio.Event()

        # send_audio que NUNCA retorna. Sin el fix, _capture_loop se queda
        # dentro de este await indefinidamente. Con el fix, wait_for lo
        # cancela al llegar al deadline.
        never = asyncio.Event()

        async def _slow_send(_frame: bytes) -> None:
            await never.wait()

        gateway.send_audio = _slow_send

        from domain.state_machine import _recording
        task = asyncio.create_task(
            _recording("wake", bus, audio, gateway, playback, cfg, cancel_event)
        )

        # Los frames llegan tras el drain (side_effect de get_preroll), así
        # que _capture_loop llama a send_audio (el camino del bug).

        # Margen generoso: deadline 0.3s + overhead asyncio (~0.05s típico)
        # + tiempo de propagación de cancel tras el break. 2s es holgado.
        # Sin el fix, este asyncio.wait_for dispara TimeoutError a los 2s
        # (el test se colgará dentro de send_audio).
        start = time.monotonic()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            pytest.fail(
                "Issue #23: _recording no terminó en 2s aunque "
                "recording_timeout_s=0.3s. send_audio bloqueante no está "
                "siendo acotado por el deadline."
            )
        elapsed = time.monotonic() - start
        # Deadline respetado: tarda ~0.3s + overhead asyncio. Damos margen
        # hasta 1.5s por si asyncio scheduler se retrasa; sigue siendo
        # muchísimo menos que 2s (umbral de pytest.fail).
        assert elapsed < 1.5, (
            f"_recording tardó {elapsed:.2f}s (esperado < 1.5s con "
            f"recording_timeout_s=0.3s)"
        )
        # Sanity: send_end debe haberse llamado al final de RECORDING.
        gateway.send_end.assert_awaited_once()

    asyncio.run(_driver())


# ---------------------------------------------------------------------------
# Test secundario (verifica que el fix no rompe el camino normal)
# ---------------------------------------------------------------------------

def test_recording_timeout_still_fires_when_send_audio_is_normal() -> None:
    """Test secundario: con send_audio rápido, el deadline sigue terminando
    RECORDING correctamente. Asegura que el fix no introduce regresiones
    en el caso normal (frames llegan, send_audio retorna, deadline se
    respeta al final del bucle).
    """

    async def _driver() -> None:
        cfg = _cfg(recording_timeout_s=0.3)
        bus = EventBus()
        # 20 frames non-silence — silence_count nunca llega al umbral,
        # así que la única salida es el deadline.
        audio, _ = _audio_mock_with_frames(n=20)
        gateway = _gateway_mock()
        playback = _playback_mock()
        cancel_event = asyncio.Event()

        # send_audio rápido (default AsyncMock retorna inmediatamente).
        from domain.state_machine import _recording
        task = asyncio.create_task(
            _recording("wake", bus, audio, gateway, playback, cfg, cancel_event)
        )

        # Los 20 frames llegan tras el drain (side_effect de get_preroll).

        start = time.monotonic()
        await asyncio.wait_for(task, timeout=2.0)
        elapsed = time.monotonic() - start

        # Debe haber terminado por deadline (~0.3s + overhead asyncio).
        # Damos margen [0.1s, 1.5s] para tolerar jitter del scheduler.
        assert 0.1 < elapsed < 1.5, (
            f"_recording tardó {elapsed:.2f}s (esperado en [0.1, 1.5]s con "
            f"recording_timeout_s=0.3s)"
        )
        gateway.send_end.assert_awaited_once()

    asyncio.run(_driver())
