"""
test_playback_engine.py — Tests de lógica pura para PlaybackEngine.

No requiere AudioBackend real: usa un mock del backend de audio.
Ejecutar desde la raíz del proyecto:
    python -m pytest client/test_playback_engine.py -v
o directamente:
    python client/test_playback_engine.py
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock


# Añadir client/ al sys.path para importar módulos locales
import os
_HERE = os.path.dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from event_bus import EventBus, VoiceEvent  # noqa: E402
from playback_engine import PlaybackEngine   # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine() -> tuple[PlaybackEngine, EventBus, list[VoiceEvent]]:
    """Devuelve (engine, bus, captured_events, audio_mock)."""
    bus = EventBus()
    captured: list[VoiceEvent] = []

    # Parchear publish para capturar eventos sin cola async
    original_publish = bus.publish

    def capturing_publish(event: VoiceEvent) -> None:
        captured.append(event)
        original_publish(event)

    bus.publish = capturing_publish  # type: ignore[method-assign]

    # Mock AudioBackend con métodos async
    audio = MagicMock()
    audio.play_chunk = AsyncMock()
    audio.play_notification = AsyncMock()
    audio.drain = AsyncMock()
    audio.reset = MagicMock()

    engine = PlaybackEngine(bus=bus, audio=audio)
    return engine, bus, captured


def _make_audio(seconds: float) -> bytes:
    """Genera bytes de audio PCM16 24kHz para la duración dada."""
    num_samples = int(seconds * 24000)
    return b"\x00\x00" * num_samples  # silencio


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPushToken(unittest.TestCase):
    def test_push_token_acumula(self) -> None:
        engine, _, _ = _make_engine()
        engine.push_token("Hola")
        engine.push_token(" mundo")
        self.assertEqual("".join(engine._text_buffer), "Hola mundo")

    def test_push_token_ignora_vacio(self) -> None:
        engine, _, _ = _make_engine()
        engine.push_token("")
        self.assertEqual(engine._text_buffer, [])

    def test_reset_limpia_buffer_y_cursor(self) -> None:
        engine, _, _ = _make_engine()
        engine.push_token("texto")
        engine._text_cursor = 3.0
        engine.reset()
        self.assertEqual(engine._text_buffer, [])
        self.assertEqual(engine._text_cursor, 0.0)


class TestPlayChunk(unittest.IsolatedAsyncioTestCase):
    async def test_play_chunk_emite_eventos_display(self) -> None:
        """play_chunk debe emitir al menos 1 evento display_text_update."""
        engine, bus, captured = _make_engine()
        engine.push_token("Hola mundo, esto es una prueba de texto largo.")

        audio = _make_audio(0.5)  # 0.5 segundos → 10 ticks de 50ms
        await engine.play_chunk(audio)

        display_events = [e for e in captured if e.type == "display_text_update"]
        self.assertGreater(len(display_events), 0, "Debe emitirse al menos 1 display_text_update")

    async def test_play_chunk_texto_crece_progresivamente(self) -> None:
        """El texto visible debe ir creciendo tick a tick."""
        engine, bus, captured = _make_engine()
        engine.push_token("ABCDEFGHIJKLMNOPQRSTUVWXYZ")  # 26 chars

        audio = _make_audio(0.5)
        await engine.play_chunk(audio)

        display_events = [e for e in captured if e.type == "display_text_update"]
        texts = [e.data["text"] for e in display_events]

        # El primer texto visible debe ser más corto que el último
        self.assertLess(len(texts[0]), len(texts[-1]),
                        f"El texto debe crecer: primer='{texts[0]}', último='{texts[-1]}'")

    async def test_play_chunk_sin_tokens_no_emite_texto_extra(self) -> None:
        """Sin tokens previos, debe emitir eventos con texto vacío (sin crash)."""
        engine, bus, captured = _make_engine()
        # No push_token

        audio = _make_audio(0.3)
        await engine.play_chunk(audio)  # No debe lanzar excepción

        display_events = [e for e in captured if e.type == "display_text_update"]
        for e in display_events:
            self.assertEqual(e.data["text"], "", "Sin tokens, texto visible debe ser vacío")

    async def test_play_chunk_cursor_no_supera_total_chars(self) -> None:
        """El cursor nunca debe avanzar más allá del total de caracteres del buffer."""
        engine, bus, captured = _make_engine()
        engine.push_token("AB")  # solo 2 chars

        audio = _make_audio(0.5)  # duración relativamente larga
        await engine.play_chunk(audio)

        display_events = [e for e in captured if e.type == "display_text_update"]
        texts = [e.data["text"] for e in display_events]

        for text in texts:
            self.assertLessEqual(len(text), 2,
                                 f"Texto '{text}' supera el buffer de 2 chars")

    async def test_play_chunk_vacio_no_reproduce(self) -> None:
        """play_chunk con bytes vacíos no debe invocar al audio backend ni emitir eventos."""
        engine, bus, captured = _make_engine()
        await engine.play_chunk(b"")

        display_events = [e for e in captured if e.type == "display_text_update"]
        self.assertEqual(len(display_events), 0)
        engine._audio.play_chunk.assert_not_called()

    async def test_duracion_calculo_correcto(self) -> None:
        """Verificar que audio_duration se calcula correctamente."""
        # 24000 muestras * 2 bytes = 48000 bytes → 1 segundo
        audio_1s = b"\x00\x00" * 24000
        duration = len(audio_1s) / (24000 * 2)
        self.assertAlmostEqual(duration, 1.0, places=10)

        # 1200 muestras * 2 bytes = 2400 bytes → 0.05 segundos (1 tick)
        audio_1tick = b"\x00\x00" * 1200
        duration_tick = len(audio_1tick) / (24000 * 2)
        self.assertAlmostEqual(duration_tick, 0.05, places=10)

    async def test_play_chunk_no_duplica_espera_del_backend(self) -> None:
        """Si el backend ya bloquea la duración real del audio, PlaybackEngine no
        debe esperar otra vez esa misma duración para animar el texto."""
        engine, bus, captured = _make_engine()
        engine.push_token("Texto de prueba para animar")

        real_duration = 0.2

        async def _slow_play_chunk(audio: bytes) -> None:
            await asyncio.sleep(real_duration)

        engine._audio.play_chunk = _slow_play_chunk

        audio = _make_audio(real_duration)
        loop = asyncio.get_running_loop()
        start = loop.time()
        await engine.play_chunk(audio)
        elapsed = loop.time() - start

        self.assertLess(
            elapsed, real_duration * 1.6,
            f"play_chunk tardó {elapsed:.3f}s (~doble de {real_duration}s) — bug de doble espera",
        )

    async def test_reset_entre_turnos(self) -> None:
        """Después de reset, play_chunk del siguiente turno empieza desde cero."""
        engine, bus, captured = _make_engine()
        engine.push_token("Primer turno de texto")
        audio = _make_audio(0.3)
        await engine.play_chunk(audio)

        # Reset para nuevo turno
        engine.reset()
        captured.clear()

        engine.push_token("Nuevo")
        audio2 = _make_audio(0.3)
        await engine.play_chunk(audio2)

        display_events = [e for e in captured if e.type == "display_text_update"]
        texts = [e.data["text"] for e in display_events]
        # Ningún evento debe contener texto del turno anterior
        for text in texts:
            self.assertNotIn("Primer", text,
                             "Texto del turno anterior no debe aparecer tras reset")


class TestCursorLogicUnit(unittest.TestCase):
    """Tests de lógica pura del cursor, sin asyncio."""

    def test_chars_per_second_formula(self) -> None:
        text_buffer = ["Hola ", "mundo"]  # 10 chars
        text_cursor = 0.0
        total_chars = sum(len(t) for t in text_buffer)
        pending_chars = total_chars - int(text_cursor)
        audio_duration = 0.5

        chars_per_second = pending_chars / audio_duration
        self.assertAlmostEqual(chars_per_second, 20.0)  # 10 chars / 0.5s

    def test_cursor_avance_parcial(self) -> None:
        text_buffer = ["ABCDE"]  # 5 chars
        text_cursor = 2.0
        total_chars = sum(len(t) for t in text_buffer)
        pending_chars = total_chars - int(text_cursor)  # 3
        audio_duration = 0.3
        chars_per_second = pending_chars / audio_duration  # 10 chars/s

        tick = 0.05
        text_cursor = min(text_cursor + chars_per_second * tick, float(total_chars))
        self.assertAlmostEqual(text_cursor, 2.5)

        visible = "".join(text_buffer)[: int(text_cursor)]
        self.assertEqual(visible, "AB")  # int(2.5) = 2

    def test_cursor_clamp_al_total(self) -> None:
        text_buffer = ["Hi"]  # 2 chars
        total_chars = 2
        text_cursor = 1.8
        chars_per_second = 100.0
        tick = 0.05

        text_cursor = min(text_cursor + chars_per_second * tick, float(total_chars))
        self.assertEqual(text_cursor, 2.0, "El cursor debe clamp-earse al total de chars")


# ---------------------------------------------------------------------------
# Punto de entrada directo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
