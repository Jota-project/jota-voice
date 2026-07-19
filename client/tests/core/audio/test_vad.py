from pathlib import Path

import numpy as np

from core.audio.vad import is_silence

FIXTURES = Path(__file__).parent.parent.parent / "fakes" / "audio_samples"


def _load(name: str) -> bytes:
    """Carga un fixture .wav (PCM int16) y lo devuelve como float32 normalizado.

    El algoritmo de is_silence() opera sobre float32 normalizado (decisión 8
    del spec de Fase A) porque así llegan los frames en el pipeline real,
    tras _int16_to_float32() en el hilo lector de AudioCapture.
    """
    import wave

    with wave.open(str(FIXTURES / name), "rb") as w:
        raw = w.readframes(w.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return samples.tobytes()


def test_silence_is_silence():
    assert is_silence(_load("silence.wav"), threshold_rms=0.01)


def test_pink_noise_is_not_silence():
    assert not is_silence(_load("pink_noise.wav"), threshold_rms=0.01)


def test_speech_is_not_silence():
    assert not is_silence(_load("speech_like.wav"), threshold_rms=0.01)


def test_threshold_boundary():
    # Subir el threshold hasta que 'speech_like' se considere silencio
    frame = _load("speech_like.wav")
    assert is_silence(frame, threshold_rms=1.0)


def test_empty_frame_is_silence():
    # Regresión issue #27: en la versión de Termux, un frame vacío daba
    # RMS=nan y `nan < threshold` es False -> se trataba como "no silencio".
    assert is_silence(b"", threshold_rms=0.01)
