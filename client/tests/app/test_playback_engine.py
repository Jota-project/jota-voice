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
from unittest.mock import create_autospec

from backends.audio_base import AudioBackend

from domain.event_bus import EventBus, VoiceEvent
from app.playback_engine import PlaybackEngine
from tests.fakes.waiting import wait_until



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

    # AudioBackend con autospec (issue #38): los métodos async son AsyncMock
    # y una llamada fuera de la interfaz hace fallar el test.
    audio = create_autospec(AudioBackend, instance=True)

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
        engine._text_cursor = 3
        engine.reset()
        self.assertEqual(engine._text_buffer, [])
        self.assertEqual(engine._text_cursor, 0)


class TestPlayChunk(unittest.IsolatedAsyncioTestCase):
    """Un evento display_text_update por chunk de audio (antes: un tick cada
    50 ms, hasta ~20 publicaciones/s empujadas a cada display). El display
    anima el reveal con chunk_text + duration_s (docs/display-protocol.md)."""

    def _display(self, captured: list[VoiceEvent]) -> list[VoiceEvent]:
        return [e for e in captured if e.type == "display_text_update"]

    async def test_un_evento_por_chunk_con_su_duracion(self) -> None:
        engine, _, captured = _make_engine()
        engine.push_token("Hola mundo, esto es una prueba de texto largo.")
        await engine.play_chunk(_make_audio(0.5))
        events = self._display(captured)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data["chunk_text"],
                         "Hola mundo, esto es una prueba de texto largo.")
        self.assertEqual(events[0].data["cumulative_text"],
                         "Hola mundo, esto es una prueba de texto largo.")
        self.assertAlmostEqual(events[0].data["duration_s"], 0.5, places=3)

    async def test_texto_crece_entre_chunks(self) -> None:
        engine, _, captured = _make_engine()
        engine.push_token("Hola")
        await engine.play_chunk(_make_audio(0.2))
        engine.push_token(" mundo")
        await engine.play_chunk(_make_audio(0.2))
        events = self._display(captured)
        self.assertEqual([e.data["chunk_text"] for e in events], ["Hola", " mundo"])
        self.assertEqual([e.data["cumulative_text"] for e in events], ["Hola", "Hola mundo"])

    async def test_sin_tokens_nuevos_chunk_text_vacio(self) -> None:
        engine, _, captured = _make_engine()
        await engine.play_chunk(_make_audio(0.3))
        engine.push_token("AB")
        await engine.play_chunk(_make_audio(0.3))
        await engine.play_chunk(_make_audio(0.3))
        events = self._display(captured)
        self.assertEqual([e.data["chunk_text"] for e in events], ["", "AB", ""])
        self.assertEqual([e.data["cumulative_text"] for e in events], ["", "AB", "AB"])

    async def test_play_chunk_vacio_no_reproduce(self) -> None:
        engine, _, captured = _make_engine()
        await engine.play_chunk(b"")
        self.assertEqual(self._display(captured), [])
        engine._audio.play_chunk.assert_not_called()

    async def test_duracion_calculo_correcto(self) -> None:
        # PCM16 mono 24 kHz: 48000 bytes = 1 s.
        engine, _, captured = _make_engine()
        await engine.play_chunk(b"\x00\x00" * 24000)
        await engine.play_chunk(b"\x00\x00" * 1200)
        durations = [e.data["duration_s"] for e in self._display(captured)]
        self.assertAlmostEqual(durations[0], 1.0, places=10)
        self.assertAlmostEqual(durations[1], 0.05, places=10)

    async def test_evento_se_publica_antes_de_reproducir(self) -> None:
        """El display recibe el chunk al empezar a sonar, no al terminar: el
        reveal dura lo que el audio."""
        engine, _, captured = _make_engine()
        engine.push_token("Texto")
        seen_at_play: list[int] = []

        async def _play(audio: bytes) -> None:
            seen_at_play.append(len(self._display(captured)))

        engine._audio.play_chunk = _play
        await engine.play_chunk(_make_audio(0.2))
        self.assertEqual(seen_at_play, [1])

    async def test_no_espera_mas_que_el_backend(self) -> None:
        """Sin animación interna: play_chunk termina en cuanto el backend
        termina (antes había un bucle de sleep(0.05) en paralelo)."""
        engine, _, _ = _make_engine()
        release = asyncio.Event()

        async def _blocking(audio: bytes) -> None:
            await release.wait()

        engine._audio.play_chunk = _blocking
        task = asyncio.create_task(engine.play_chunk(_make_audio(5.0)))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        await asyncio.wait_for(task, timeout=1.0)

    async def test_reset_entre_turnos(self) -> None:
        engine, _, captured = _make_engine()
        engine.push_token("Primer turno de texto")
        await engine.play_chunk(_make_audio(0.3))
        engine.reset()
        captured.clear()
        engine.push_token("Nuevo")
        await engine.play_chunk(_make_audio(0.3))
        for e in self._display(captured):
            self.assertNotIn("Primer", e.data["chunk_text"])
            self.assertNotIn("Primer", e.data["cumulative_text"])
        self.assertEqual(self._display(captured)[0].data["cumulative_text"], "Nuevo")


class TestResetConcurrente(unittest.IsolatedAsyncioTestCase):
    """Issue #89: reset() es síncrono (lo llama state_machine sin await) y no
    puede tomar el asyncio.Lock de play_chunk/play_notification/drain. Lo que
    estuviera esperando ese lock cuando llega un reset() pertenece al turno
    anterior y no debe llegar al backend."""

    async def _blocked_engine(self):
        engine, _, _ = _make_engine()
        release = asyncio.Event()
        played: list[bytes] = []

        async def _play(audio: bytes) -> None:
            played.append(audio)
            await release.wait()

        engine._audio.play_chunk.side_effect = _play
        return engine, release, played

    async def test_chunk_en_espera_se_descarta_tras_reset(self) -> None:
        engine, release, played = await self._blocked_engine()
        first, second = b"\x01\x00" * 10, b"\x02\x00" * 10
        t1 = asyncio.create_task(engine.play_chunk(first))
        await wait_until(lambda: played == [first])
        t2 = asyncio.create_task(engine.play_chunk(second))
        await wait_until(lambda: engine._play_lock._waiters)  # t2 espera el lock

        engine.reset()
        release.set()
        await asyncio.gather(t1, t2)

        self.assertEqual(played, [first], "audio del turno anterior sonó tras reset()")
        engine._audio.reset.assert_called_once_with()

    async def test_notificacion_en_espera_se_descarta_tras_reset(self) -> None:
        engine, release, played = await self._blocked_engine()
        t1 = asyncio.create_task(engine.play_chunk(b"\x01\x00"))
        await wait_until(lambda: len(played) == 1)
        t2 = asyncio.create_task(engine.play_notification())
        await wait_until(lambda: engine._play_lock._waiters)

        engine.reset()
        release.set()
        await asyncio.gather(t1, t2)

        engine._audio.play_notification.assert_not_called()

    async def test_chunk_posterior_al_reset_si_suena(self) -> None:
        """El primer chunk del turno nuevo (pedido después del reset) no se
        pierde aunque el lock siga ocupado por el turno anterior."""
        engine, release, played = await self._blocked_engine()
        old, new = b"\x01\x00", b"\x02\x00"
        t1 = asyncio.create_task(engine.play_chunk(old))
        await wait_until(lambda: played == [old])

        engine.reset()
        t2 = asyncio.create_task(engine.play_chunk(new))
        await wait_until(lambda: engine._play_lock._waiters)
        release.set()
        await asyncio.gather(t1, t2)

        self.assertEqual(played, [old, new])
