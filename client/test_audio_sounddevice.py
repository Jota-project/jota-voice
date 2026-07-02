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
