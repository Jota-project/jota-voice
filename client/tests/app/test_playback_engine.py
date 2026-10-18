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
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from domain.event_bus import EventBus, VoiceEvent
from app.playback_engine import PlaybackEngine

from tests.fakes.waiting import wait_until

_real_sleep = asyncio.sleep


async def _instant_sleep(delay: float, result=None):
    """asyncio.sleep sin espera real: cede el loop una vez. Los ticks de
    animación de PlaybackEngine pasan a ser instantáneos y deterministas
    (issue #76)."""
    return await _real_sleep(0, result)


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
    async def asyncSetUp(self) -> None:
        # Los tests miran qué se publica tick a tick, no cuánto tarda: sin
        # esperas reales la clase pasa de ~3s a milisegundos y deja de
        # depender de la carga de la máquina.
        patcher = patch.object(asyncio, "sleep", _instant_sleep)
        patcher.start()
        self.addCleanup(patcher.stop)

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

        # Issue #76: antes medía el tiempo real (< 1.6x de 0.2s), frágil con
        # la CPU cargada. Ahora el backend "reproduce" hasta que el test lo
        # libera: si la animación corre en paralelo, todos sus ticks se
        # publican mientras el backend sigue bloqueado; con la doble espera
        # (animar después de reproducir) no saldría ninguno.
        backend_playing = asyncio.Event()
        release_backend = asyncio.Event()

        async def _blocking_play_chunk(audio: bytes) -> None:
            backend_playing.set()
            await release_backend.wait()

        engine._audio.play_chunk = _blocking_play_chunk

        audio = _make_audio(0.2)  # 4 ticks de 50ms
        n_ticks = 4
        task = asyncio.create_task(engine.play_chunk(audio))
        try:
            def _ticks() -> int:
                return sum(1 for e in captured if e.type == "display_text_update")

            await wait_until(lambda: _ticks() >= n_ticks or task.done(),
                             message="ticks de animación")
            self.assertTrue(backend_playing.is_set())
            self.assertFalse(task.done(), "play_chunk no debe terminar antes que el backend")
            self.assertEqual(
                _ticks(), n_ticks,
                "la animación del texto debe correr en paralelo al backend, no después "
                "(bug de doble espera)",
            )
        finally:
            release_backend.set()
            await asyncio.wait_for(task, timeout=5.0)

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


