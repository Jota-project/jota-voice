"""VAD por RMS — detección de silencio sobre frames float32 normalizados."""

import numpy as np


def is_silence(frame: bytes, threshold_rms: float) -> bool:
    """Devuelve True si el frame está por debajo del umbral de VAD.

    El frame se interpreta como float32 normalizado en [-1.0, 1.0].
    Un frame vacío se considera silencio (issue #27: antes daba RMS=nan
    y `nan < threshold` es False, tratándolo como "no silencio").
    """
    samples = np.frombuffer(frame, dtype=np.float32)
    if samples.size == 0:
        return True
    rms = float(np.sqrt(np.mean(samples**2)))
    return rms < threshold_rms
