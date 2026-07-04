"""Síntesis del tono de notificación — compartida entre backends de audio."""
from __future__ import annotations

import numpy as np

_SEGMENTS = [(587.0, 0.10), (880.0, 0.18)]
_GAP_S = 0.03


def synth_notification_tone(rate: int) -> bytes:
    """Genera el beep de dos tonos ascendentes (~300ms) como PCM16 mono."""
    segments = []
    for freq, dur in _SEGMENTS:
        n = int(rate * dur)
        t = np.linspace(0, dur, n, endpoint=False)
        envelope = np.exp(-t * 18.0)
        tone = np.sin(2 * np.pi * freq * t) * 0.6 + np.sin(2 * np.pi * freq * 2 * t) * 0.2
        segments.append((tone * envelope * 32767 * 0.9).astype(np.int16))
        segments.append(np.zeros(int(rate * _GAP_S), dtype=np.int16))
    return np.concatenate(segments).tobytes()
