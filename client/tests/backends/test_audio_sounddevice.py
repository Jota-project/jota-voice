"""Tests offline del SounddeviceBackend (VAD + preroll + ring-buffer).

No abre streams reales (no requiere hardware); solo valida la lógica de
is_silence() y get_preroll() con frames sintéticos.
"""
from __future__ import annotations

import asyncio

import numpy as np

from config import AudioConfig
from backends.audio_sounddevice import SounddeviceBackend


def _cfg(**kwargs) -> AudioConfig:
    base = dict(
        sample_rate=16000,
        channels=1,
        frames_per_buffer=512,
        preroll_seconds=1.5,
        vad_rms_threshold=200.0,
        input_device=None,
        output_device=None,
    )
    base.update(kwargs)
    return AudioConfig(**base)


def _silence_frame(samples: int = 512) -> bytes:
    return np.zeros(samples, dtype=np.float32).tobytes()


def _tone_frame(samples: int = 512, amplitude: float = 0.5) -> bytes:
    t = np.arange(samples) / 16000.0
    wave = (np.sin(2 * np.pi * 440.0 * t) * amplitude).astype(np.float32)
    return wave.tobytes()


def test_is_silence_silent_frame() -> None:
    be = SounddeviceBackend(_cfg())
    assert be.is_silence(_silence_frame()) is True


def test_is_silence_loud_frame() -> None:
    be = SounddeviceBackend(_cfg())
    assert be.is_silence(_tone_frame(amplitude=0.5)) is False


def test_get_preroll_empty_at_init() -> None:
    be = SounddeviceBackend(_cfg())
    assert be.get_preroll() == b""


def test_get_preroll_returns_recent_frames() -> None:
    be = SounddeviceBackend(_cfg())
    # Inyectar 10 frames en el ring-buffer interno (sin abrir stream)
    for _ in range(10):
        be._preroll.append(_tone_frame(amplitude=0.1))
    preroll = be.get_preroll()
    assert len(preroll) == 10 * 512 * 4  # 10 frames × 512 samples × 4 bytes (float32)


def test_preroll_maxlen_respects_seconds() -> None:
    cfg = _cfg(frames_per_buffer=512, sample_rate=16000, preroll_seconds=1.0)
    be = SounddeviceBackend(cfg)
    # maxlen = 1.0 * 16000 / 512 = ~31 frames
    assert be._preroll.maxlen == 31


def test_reset_clears_preroll() -> None:
    be = SounddeviceBackend(_cfg())
    be._preroll.append(_tone_frame())
    be.reset()
    assert be.get_preroll() == b""


def test_out_callback_cola_vacia_rellena_silencio() -> None:
    """Bug: outdata[:] = bytes crudos lanzaba ValueError en vez de escribir silencio."""
    be = SounddeviceBackend(_cfg())
    frames = 8
    outdata = np.ones((frames, 1), dtype=np.int16)
    be._out_callback(outdata, frames, None, None)  # no debe lanzar
    assert np.all(outdata == 0)


def test_out_callback_chunk_impar_no_lanza() -> None:
    """Bug real (2026-07-09): un chunk TTS de longitud impar de bytes hacía
    que np.frombuffer(..., dtype=np.int16) lanzara ValueError dentro del
    callback de PortAudio. sounddevice registra ese callback con
    error=paAbort, así que una excepción sin capturar aborta el
    OutputStream ENTERO — no solo ese chunk — dejando el resto de la
    respuesta en silencio permanente sin ningún error visible."""
    be = SounddeviceBackend(_cfg())
    frames = 8
    chunk = bytes([1, 2, 3])  # 3 bytes: no es múltiplo de 2 (tamaño de int16)
    be._play_q.put(chunk)

    outdata = np.zeros((frames, 1), dtype=np.int16)
    be._out_callback(outdata, frames, None, None)  # no debe lanzar


def test_out_callback_no_trunca_chunk_mayor_que_frames() -> None:
    """Bug: un chunk TTS más grande que `frames` perdía silenciosamente el resto."""
    be = SounddeviceBackend(_cfg())
    frames = 4
    chunk = np.arange(1, 11, dtype=np.int16).tobytes()  # 10 samples, > frames
    be._play_q.put(chunk)

    outdata1 = np.zeros((frames, 1), dtype=np.int16)
    be._out_callback(outdata1, frames, None, None)
    assert list(outdata1.ravel()) == [1, 2, 3, 4]

    outdata2 = np.zeros((frames, 1), dtype=np.int16)
    be._out_callback(outdata2, frames, None, None)
    assert list(outdata2.ravel()) == [5, 6, 7, 8]

    outdata3 = np.zeros((frames, 1), dtype=np.int16)
    be._out_callback(outdata3, frames, None, None)
    assert list(outdata3.ravel()) == [9, 10, 0, 0]


def test_play_chunk_nunca_encola_bytes_impares() -> None:
    """Fix de raíz: el chunking de red no garantiza alinear cada mensaje a
    un número par de bytes (tamaño de una muestra int16). play_chunk debe
    arrastrar el byte suelto al siguiente chunk en vez de dejar que un
    chunk de longitud impar llegue a _play_q (ver test_out_callback_chunk_impar_no_lanza)."""
    be = SounddeviceBackend(_cfg(output_device=None))
    be._ensure_output_stream = lambda: None  # evita abrir hardware real

    async def _run():
        await be.play_chunk(bytes([1, 2, 3]))  # 3 bytes: impar
        await be.play_chunk(bytes([4, 5]))     # 2 bytes: par

    asyncio.run(_run())

    queued = []
    while not be._play_q.empty():
        queued.append(be._play_q.get_nowait())

    assert all(len(c) % 2 == 0 for c in queued), f"chunk impar encolado: {queued}"
    # El byte 5 final queda pendiente como carry (aún no tiene pareja para
    # formar una muestra int16 completa) — no se pierde, ninguno de los
    # bytes 1-4 se reordena ni se descarta.
    assert b"".join(queued) == bytes([1, 2, 3, 4])
    assert be._enqueue_carry == bytes([5])
