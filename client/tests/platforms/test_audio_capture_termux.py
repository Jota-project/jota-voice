"""Tests offline de AudioCapture (platforms/termux/audio_capture_termux.py).

Issue #36: bucle de reintento con backoff de parec, cierre limpio del
proceso/hilo y conversión int16 -> float32, sin Termux ni parec reales.
`subprocess.Popen` se sustituye por un mock con autospec y `asyncio.sleep`
por un fake que registra los retardos sin esperar tiempo real.
"""
from __future__ import annotations

import asyncio
import io
import logging
import subprocess
import threading
from unittest.mock import MagicMock, create_autospec

import numpy as np
import pytest

from config import AudioConfig
from platforms.termux import audio_capture_termux as mod
from platforms.termux.audio_capture_termux import AudioCapture

_FRAMES_PER_BUFFER = 4  # 8 bytes int16 por frame: datos de test pequeños


def _cfg(**kwargs) -> AudioConfig:
    base = dict(
        sample_rate=16000,
        channels=1,
        frames_per_buffer=_FRAMES_PER_BUFFER,
        preroll_seconds=1.5,
        vad_rms_threshold=200.0,
        input_device=None,
        output_device=None,
    )
    base.update(kwargs)
    return AudioConfig(**base)


def _fake_proc(*, alive: bool = True, stdout: bytes = b"") -> MagicMock:
    """Proceso parec falso. `alive=False` simula que muere al arrancar."""
    proc = create_autospec(subprocess.Popen, instance=True)
    proc.poll.return_value = None if alive else 1
    proc.returncode = None if alive else 1
    proc.stdout = io.BytesIO(stdout)
    return proc


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """Sustituye asyncio.sleep por un fake que registra el retardo pedido y
    solo cede el control al loop (sin esperar tiempo real)."""
    real_sleep = asyncio.sleep
    recorded: list[float] = []

    async def _fake_sleep(delay, *args, **kwargs):  # noqa: ARG001
        recorded.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(mod.asyncio, "sleep", _fake_sleep)
    return recorded


def _patch_popen(monkeypatch, *side_effect) -> MagicMock:
    popen = create_autospec(subprocess.Popen, side_effect=list(side_effect))
    monkeypatch.setattr(mod.subprocess, "Popen", popen)
    return popen


def _join_reader(cap: AudioCapture) -> None:
    assert cap._read_thread is not None
    cap._read_thread.join(timeout=2.0)
    assert not cap._read_thread.is_alive(), "el hilo lector no terminó"


async def _drain_loop_callbacks() -> None:
    """Procesa los call_soon_threadsafe pendientes del hilo lector."""
    for _ in range(3):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# start(): arranque y bucle de reintento
# ---------------------------------------------------------------------------


def test_start_lanza_parec_con_parametros_de_config(monkeypatch, sleeps) -> None:
    monkeypatch.setenv("PULSE_RUNTIME_PATH", "/tmp/pulse-test")
    proc = _fake_proc()
    popen = _patch_popen(monkeypatch, proc)
    cap = AudioCapture(_cfg(sample_rate=22050))

    async def _run() -> None:
        await cap.start()
        _join_reader(cap)

    asyncio.run(_run())

    popen.assert_called_once()
    args, kwargs = popen.call_args
    cmd = args[0]
    assert cmd[0] == "parec"
    assert "--device=OpenSL_ES_source" in cmd
    assert "--rate=22050" in cmd
    assert "--channels=1" in cmd
    assert "--format=s16le" in cmd
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["env"]["PULSE_RUNTIME_PATH"] == "/tmp/pulse-test"
    assert cap._proc is proc
    # Solo la espera de verificación de arranque: sin reintentos.
    assert sleeps == [0.5]


def test_start_pulse_runtime_path_por_defecto(monkeypatch, sleeps) -> None:
    monkeypatch.delenv("PULSE_RUNTIME_PATH", raising=False)
    monkeypatch.setenv("HOME", "/home/jota")
    popen = _patch_popen(monkeypatch, _fake_proc())
    cap = AudioCapture(_cfg())

    async def _run() -> None:
        await cap.start()
        _join_reader(cap)

    asyncio.run(_run())

    assert popen.call_args.kwargs["env"]["PULSE_RUNTIME_PATH"] == "/home/jota/.pulse"


def test_start_reintenta_con_backoff_exponencial_acotado(monkeypatch, sleeps) -> None:
    """Si parec muere al arrancar se relanza con backoff 1, 2, 4, 8 y
    tope de 10 s, intercalado con la espera de verificación de 0.5 s."""
    dead = [_fake_proc(alive=False) for _ in range(5)]
    good = _fake_proc()
    popen = _patch_popen(monkeypatch, *dead, good)
    cap = AudioCapture(_cfg())

    async def _run() -> None:
        await cap.start()
        _join_reader(cap)

    asyncio.run(_run())

    assert popen.call_count == 6
    assert sleeps == [0.5, 1.0, 0.5, 2.0, 0.5, 4.0, 0.5, 8.0, 0.5, 10.0, 0.5]
    assert cap._proc is good


def test_start_reintenta_si_popen_lanza_excepcion_generica(monkeypatch, sleeps) -> None:
    good = _fake_proc()
    popen = _patch_popen(monkeypatch, OSError("EAGAIN"), OSError("EAGAIN"), good)
    cap = AudioCapture(_cfg())

    async def _run() -> None:
        await cap.start()
        _join_reader(cap)

    asyncio.run(_run())

    assert popen.call_count == 3
    assert sleeps == [1.0, 2.0, 0.5]
    assert cap._proc is good


def test_start_parec_no_encontrado_propaga_sin_reintentar(monkeypatch, sleeps) -> None:
    popen = _patch_popen(monkeypatch, FileNotFoundError("parec"))
    cap = AudioCapture(_cfg())

    with pytest.raises(FileNotFoundError):
        asyncio.run(cap.start())

    popen.assert_called_once()
    assert sleeps == []
    assert cap._read_thread is None


def test_stop_durante_reintentos_corta_el_bucle(monkeypatch) -> None:
    """Si se pide parar mientras parec sigue muriendo, start() debe
    terminar en vez de reintentar para siempre."""
    real_sleep = asyncio.sleep
    cap = AudioCapture(_cfg())

    async def _sleep_que_para(delay, *args, **kwargs):  # noqa: ARG001
        if delay >= 1.0:  # espera de backoff: simula un stop() concurrente
            cap._stop_event.set()
        await real_sleep(0)

    monkeypatch.setattr(mod.asyncio, "sleep", _sleep_que_para)
    popen = _patch_popen(monkeypatch, *[_fake_proc(alive=False) for _ in range(3)])

    asyncio.run(asyncio.wait_for(cap.start(), timeout=2.0))

    assert popen.call_count == 1
    assert cap._proc is None
    assert cap._read_thread is None


# ---------------------------------------------------------------------------
# Hilo lector: conversión int16 -> float32 y fan-out
# ---------------------------------------------------------------------------


def test_read_loop_convierte_int16_a_float32_y_hace_fanout(monkeypatch, sleeps) -> None:
    frame1 = np.array([16384, -32768, 0, 32767], dtype=np.int16).tobytes()
    frame2 = np.array([-16384, 8192, 1, -1], dtype=np.int16).tobytes()
    _patch_popen(monkeypatch, _fake_proc(stdout=frame1 + frame2))
    cap = AudioCapture(_cfg())

    async def _run() -> tuple[list[bytes], list[bytes]]:
        await cap.start()
        _join_reader(cap)
        await _drain_loop_callbacks()
        q, oww_q = cap.get_queue(), cap.get_oww_queue()
        return (
            [q.get_nowait() for _ in range(q.qsize())],
            [oww_q.get_nowait() for _ in range(oww_q.qsize())],
        )

    rec, oww = asyncio.run(_run())

    assert len(rec) == 2
    assert rec == oww  # ambas colas reciben todos los frames
    np.testing.assert_allclose(
        np.frombuffer(rec[0], np.float32), [0.5, -1.0, 0.0, 32767 / 32768.0]
    )
    np.testing.assert_allclose(
        np.frombuffer(rec[1], np.float32), [-0.5, 0.25, 1 / 32768.0, -1 / 32768.0]
    )
    assert cap.get_preroll() == rec[0] + rec[1]


def test_read_loop_termina_si_parec_cierra_stdout(monkeypatch, sleeps, caplog) -> None:
    _patch_popen(monkeypatch, _fake_proc(stdout=b""))
    cap = AudioCapture(_cfg())

    async def _run() -> int:
        await cap.start()
        _join_reader(cap)
        await _drain_loop_callbacks()
        return cap.get_queue().qsize()

    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        qsize = asyncio.run(_run())

    assert qsize == 0
    assert any("cerró stdout" in r.getMessage() for r in caplog.records)


def test_read_loop_termina_si_la_lectura_falla(monkeypatch, sleeps, caplog) -> None:
    proc = _fake_proc()
    proc.stdout = create_autospec(io.BufferedReader, instance=True)
    proc.stdout.read.side_effect = OSError("broken pipe")
    _patch_popen(monkeypatch, proc)
    cap = AudioCapture(_cfg())

    async def _run() -> int:
        await cap.start()
        _join_reader(cap)
        await _drain_loop_callbacks()
        return cap.get_queue().qsize()

    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        qsize = asyncio.run(_run())

    assert qsize == 0
    proc.stdout.read.assert_called_once_with(_FRAMES_PER_BUFFER * 2)
    assert any("error leyendo parec" in r.getMessage() for r in caplog.records)


def test_on_frame_con_loop_cerrado_solo_alimenta_preroll() -> None:
    """Tras cerrar el loop (shutdown) el hilo lector puede entregar un
    último frame: no debe lanzar, solo guardarlo en el preroll."""
    cap = AudioCapture(_cfg())
    loop = asyncio.new_event_loop()
    cap._loop = loop
    cap._queue = asyncio.Queue()
    cap._oww_queue = asyncio.Queue()
    loop.close()

    cap._on_frame(b"\x00" * 16)

    assert cap.get_preroll() == b"\x00" * 16


# ---------------------------------------------------------------------------
# stop(): cierre limpio del proceso y del hilo
# ---------------------------------------------------------------------------


def _capture_running(proc: MagicMock) -> tuple[AudioCapture, MagicMock]:
    cap = AudioCapture(_cfg())
    thread = create_autospec(threading.Thread, instance=True)
    cap._proc = proc
    cap._read_thread = thread
    return cap, thread


def test_stop_termina_parec_y_une_el_hilo() -> None:
    proc = _fake_proc()
    cap, thread = _capture_running(proc)

    asyncio.run(cap.stop())

    proc.terminate.assert_called_once_with()
    proc.wait.assert_called_once_with(timeout=2.0)
    proc.kill.assert_not_called()
    thread.join.assert_called_once_with(timeout=2.0)
    assert cap._stop_event.is_set()
    assert cap._proc is None
    assert cap._read_thread is None


def test_stop_sin_start_es_noop() -> None:
    cap = AudioCapture(_cfg())

    asyncio.run(cap.stop())

    assert cap._proc is None
    assert cap._read_thread is None


# ---------------------------------------------------------------------------
# Accesores
# ---------------------------------------------------------------------------


def test_get_queue_antes_de_start_lanza() -> None:
    cap = AudioCapture(_cfg())

    with pytest.raises(RuntimeError, match="antes de start"):
        cap.get_queue()
    with pytest.raises(RuntimeError, match="antes de start"):
        cap.get_oww_queue()


def test_is_silence_normaliza_umbral_int16_a_float32() -> None:
    """vad_rms_threshold viene en unidades int16 (200) y los frames son
    float32 normalizados: un RMS de 100/32768 es silencio, 1000/32768 no."""
    cap = AudioCapture(_cfg(vad_rms_threshold=200.0))
    quiet = np.full(64, 100 / 32768.0, dtype=np.float32).tobytes()
    loud = np.full(64, 1000 / 32768.0, dtype=np.float32).tobytes()

    assert cap.is_silence(quiet) is True
    assert cap.is_silence(loud) is False


def test_reset_vacia_el_preroll() -> None:
    cap = AudioCapture(_cfg())
    cap._preroll.append(b"\x01" * 16)

    cap.reset()

    assert cap.get_preroll() == b""
