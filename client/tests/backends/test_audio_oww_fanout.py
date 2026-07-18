"""Test de integración — issue #9: cola de audio compartida entre OWW y captura.

Antes del fix, `OWWClient._send_audio_loop` (backends/oww_client.py) y el
consumidor de RECORDING (domain/state_machine.py::_capture_loop) hacían
`await q.get()` sobre la MISMA `asyncio.Queue` devuelta por
`AudioBackend.get_queue()`. `asyncio.Queue.get()` entrega cada item a un solo
consumidor — ambos competían por cada frame, cada uno se llevaba una fracción
no determinista.

Este test ejercita el consumidor real de OWW (`OWWClient._send_audio_loop`,
sin mockear) y un consumidor equivalente al de RECORDING, corriendo
simultáneamente sobre el mismo `SounddeviceBackend`. Tras el fix, cada uno
debe recibir TODOS los frames — sin pérdidas ni robos mutuos — porque cada
consumidor tiene su propia cola independiente, alimentada por el mismo punto
de captura.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from config import AudioConfig, OWWConfig
from backends.audio_sounddevice import SounddeviceBackend
from backends.oww_client import OWWClient


def _cfg() -> AudioConfig:
    return AudioConfig(
        sample_rate=16000,
        channels=1,
        frames_per_buffer=512,
        preroll_seconds=1.5,
        vad_rms_threshold=200.0,
        input_device=None,
        output_device=None,
    )


async def _test_oww_and_recording_consumers_reciben_todos_los_frames() -> None:
    be = SounddeviceBackend(_cfg())
    # Simula lo que start() prepara, sin abrir hardware real (igual que el
    # resto de tests de este backend).
    be._loop = asyncio.get_running_loop()
    be._capture_q = asyncio.Queue()
    be._oww_q = asyncio.Queue()

    n_frames = 20
    frames = [bytes([i % 256]) * (512 * 4) for i in range(n_frames)]
    for f in frames:
        be._on_frame(f)  # ídem al callback real de PortAudio

    # Consumidor RECORDING: mismo patrón que domain/state_machine.py::_capture_loop.
    recording_received: list[bytes] = []

    async def _recording_consumer() -> None:
        q = be.get_queue()
        for _ in range(n_frames):
            recording_received.append(await q.get())

    # Consumidor OWW real (sin mockear _send_audio_loop): solo se mockea el
    # envío TCP (send_audio), que no es lo que este test verifica.
    client = OWWClient(OWWConfig())
    sent_to_oww: list[bytes] = []
    client.send_audio = AsyncMock(side_effect=lambda pcm: sent_to_oww.append(pcm))

    send_task = asyncio.create_task(client._send_audio_loop(be))
    try:
        await asyncio.wait_for(_recording_consumer(), timeout=1.0)
        # Deja que el loop de OWW procese los frames ya encolados en su cola.
        await asyncio.sleep(0.05)
    finally:
        send_task.cancel()
        try:
            await send_task
        except asyncio.CancelledError:
            pass

    assert len(recording_received) == n_frames, (
        f"RECORDING perdió frames: recibió {len(recording_received)}/{n_frames} "
        f"(robados por el consumidor de OWW)"
    )
    assert len(sent_to_oww) == n_frames, (
        f"OWW perdió frames: recibió {len(sent_to_oww)}/{n_frames} "
        f"(robados por el consumidor de RECORDING)"
    )


def test_oww_and_recording_consumers_reciben_todos_los_frames() -> None:
    asyncio.run(_test_oww_and_recording_consumers_reciben_todos_los_frames())
