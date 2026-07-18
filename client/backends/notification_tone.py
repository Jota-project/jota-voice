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
        # El decaimiento exponencial por sí solo no llega a cero al final
        # del segmento (p.ej. ~16% de amplitud aún para el tono de 100ms):
        # el salto brusco justo antes del hueco de silencio siguiente suena
        # como un clic. Forzamos un fade-out explícito en los últimos ~5ms
        # para que el segmento SIEMPRE termine en cero, sin depender de
        # ajustar a mano la constante de decaimiento.
        fade_len = min(n, max(1, int(rate * 0.005)))
        fade = np.ones(n)
        fade[n - fade_len:] = np.linspace(1.0, 0.0, fade_len)
        envelope = envelope * fade
        tone = np.sin(2 * np.pi * freq * t) * 0.6 + np.sin(2 * np.pi * freq * 2 * t) * 0.2
        segments.append((tone * envelope * 32767 * 0.9).astype(np.int16))
        segments.append(np.zeros(int(rate * _GAP_S), dtype=np.int16))
    return np.concatenate(segments).tobytes()
