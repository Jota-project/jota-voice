"""Conversión de frames PCM entre formatos."""

import numpy as np


def int16_to_float32(data: bytes) -> bytes:
    """Convierte frames PCM int16 a float32 normalizado [-1.0, 1.0]."""
    arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    return arr.tobytes()
