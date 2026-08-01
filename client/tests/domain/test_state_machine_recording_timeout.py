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
from unittest.mock import AsyncMock, MagicMock

import pytest

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
    """Devuelve (audio_mock, queue). La queue está VACÍA inicialmente.

    Razón: _recording drena la cola al entrar (líneas 218-220 de
    state_machine.py) para descartar el backlog acumulado durante _idle.
    Si la cola ya tiene frames al llamar a audio.get_queue(), el drain se
    los come ANTES de que _capture_loop arranque — y luego _capture_loop
    se queda en wait_for(q.get(), timeout=0.1) hasta el deadline sin
    pasar nunca por send_audio (que es lo que queremos testear).

    El test mete frames en la cola DESPUÉS del drain usando un callback
    de side_effect sobre audio.get_queue: la primera llamada (drain) ve
    cola vacía, las siguientes (_capture_loop) reciben una cola con
    frames.
    """
    audio = MagicMock()
    q: asyncio.Queue[bytes] = asyncio.Queue()
    # La cola se queda vacía. El test la rellena tras el drain.
    audio.get_preroll.return_value = b""
    audio.is_silence.return_value = False  # nunca silencio → solo el deadline termina

    # side_effect: la primera llamada devuelve cola vacía (drain se la
    # come — q.empty() es True inmediatamente y no drena nada). Las
    # llamadas posteriores (_capture_loop) reciben una cola con frames.
    calls = {"count": 0}

    def _get_q() -> asyncio.Queue[bytes]:
        calls["count"] += 1
        if calls["count"] == 1:
            return q  # vacía — drain no hace nada
        # Segunda+ llamadas: misma cola, pero el test ya la habrá rellenado.
        return q

    audio.get_queue.side_effect = _get_q
    return audio, q


def _gateway_mock() -> MagicMock:
    gateway = MagicMock()
    gateway.connect = AsyncMock()
    gateway.disconnect = AsyncMock()
    gateway.send_audio = AsyncMock()
    gateway.send_end = AsyncMock()
    gateway.send_text = AsyncMock()
    gateway.send_cancel = AsyncMock()
    return gateway


def _playback_mock() -> MagicMock:
    playback = MagicMock()
    playback.push_token = MagicMock()
    playback.play_chunk = AsyncMock()
    playback.play_notification = AsyncMock()
    playback.drain = AsyncMock()
    playback.reset = MagicMock()
    return playback


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
        audio, q = _audio_mock_with_frames(n=5)
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

        # Dejar que _recording ejecute el drain (cola vacía → no hace nada),
        # llame a connect() y cree _capture_loop. El primer await de
        # _capture_loop es wait_for(q.get(), timeout=0.1) — necesitamos que
        # los frames estén YA en la cola para que send_audio se llame
        # (que es el camino donde está el bug).
        await asyncio.sleep(0.05)
        for _ in range(5):
            q.put_nowait(b"\x00" * 100)

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
        audio, q = _audio_mock_with_frames(n=20)
        gateway = _gateway_mock()
        playback = _playback_mock()
        cancel_event = asyncio.Event()

        # send_audio rápido (default AsyncMock retorna inmediatamente).
        from domain.state_machine import _recording
        task = asyncio.create_task(
            _recording("wake", bus, audio, gateway, playback, cfg, cancel_event)
        )

        # Igual que el test anterior: dejar pasar el drain, luego rellenar.
        await asyncio.sleep(0.05)
        for _ in range(20):
            q.put_nowait(b"\x00" * 100)

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
