"""Tests del helper compartido de síntesis del tono de notificación."""
from __future__ import annotations

import numpy as np

from backends.notification_tone import synth_notification_tone


def test_synth_notification_tone_produces_pcm16_bytes() -> None:
    wave = synth_notification_tone(24000)
    assert isinstance(wave, bytes)
    assert len(wave) > 0
    assert len(wave) % 2 == 0  # PCM16 mono → múltiplo de 2 bytes


def test_synth_notification_tone_matches_expected_sample_count() -> None:
    rate = 24000
    wave = synth_notification_tone(rate)
    samples = np.frombuffer(wave, dtype=np.int16)
    expected = int(rate * 0.10) + int(rate * 0.03) + int(rate * 0.18) + int(rate * 0.03)
    assert len(samples) == expected


def test_synth_notification_tone_has_silent_gap_between_tones() -> None:
    rate = 24000
    wave = synth_notification_tone(rate)
    samples = np.frombuffer(wave, dtype=np.int16)
    first_tone_len = int(rate * 0.10)
    gap_len = int(rate * 0.03)
    gap = samples[first_tone_len:first_tone_len + gap_len]
    assert np.all(gap == 0)


def test_synth_notification_tone_scales_with_rate() -> None:
    wave_low = synth_notification_tone(16000)
    wave_high = synth_notification_tone(48000)
    assert len(wave_high) > len(wave_low)


def test_synth_notification_tone_no_clic_al_final_de_cada_segmento() -> None:
    """Bug real (2026-07-09): el envelope exponencial (exp(-t*18)) no llega
    a cero al final de cada segmento (~16% de amplitud para el tono de
    100ms) — el salto brusco justo antes del hueco de silencio siguiente
    sonaba como un clic, igual que el problema de los chunks TTS pero
    aislado en el propio sintetizador del beep."""
    rate = 24000
    wave = synth_notification_tone(rate)
    samples = np.frombuffer(wave, dtype=np.int16)

    first_tone_len = int(rate * 0.10)
    gap_len = int(rate * 0.03)
    second_tone_len = int(rate * 0.18)

    # Últimas muestras del primer tono, justo antes del primer hueco
    end_of_first_tone = samples[first_tone_len - 5:first_tone_len]
    assert np.all(np.abs(end_of_first_tone) < 500), (
        f"salto brusco al final del primer tono: {end_of_first_tone}"
    )

    # Últimas muestras del segundo tono, justo antes del segundo hueco
    second_tone_start = first_tone_len + gap_len
    end_of_second_tone = samples[
        second_tone_start + second_tone_len - 5:second_tone_start + second_tone_len
    ]
    assert np.all(np.abs(end_of_second_tone) < 500), (
        f"salto brusco al final del segundo tono: {end_of_second_tone}"
    )
