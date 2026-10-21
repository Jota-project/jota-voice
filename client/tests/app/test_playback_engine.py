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

def _make_engine(**kwargs) -> tuple[PlaybackEngine, EventBus, list[VoiceEvent]]:
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

    engine = PlaybackEngine(bus=bus, audio=audio, **kwargs)
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

    def test_reset_limpia_buffer_y_texto_acumulado(self) -> None:
        engine, _, _ = _make_engine()
        engine.push_token("texto")
        engine._full_text = "previo"
        engine.reset()
        self.assertEqual(engine._text_buffer, [])
        self.assertEqual(engine._full_text, "")


class TestPlayChunk(unittest.IsolatedAsyncioTestCase):
    """Un evento display_text_update por chunk de audio (antes: un tick cada
    50 ms, hasta ~20 publicaciones/s empujadas a cada display). El display
    anima el reveal con chunk_text + duration_s (docs/display-protocol.md)."""

    def _display(self, captured: list[VoiceEvent]) -> list[VoiceEvent]:
        return [e for e in captured if e.type == "display_text_update"]

    async def test_un_evento_por_chunk_con_su_duracion(self) -> None:
        engine, _, captured = _make_engine()
        engine.push_token("Hola mundo, esto es una prueba.")
        await engine.play_chunk(_make_audio(3.0))
        events = self._display(captured)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data["chunk_text"], "Hola mundo, esto es una prueba.")
        self.assertEqual(events[0].data["cumulative_text"], "Hola mundo, esto es una prueba.")
        self.assertAlmostEqual(events[0].data["duration_s"], 3.0, places=3)

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


class TestTextoIncremental(unittest.IsolatedAsyncioTestCase):
    """Issue #95: play_chunk hacía "".join() de TODOS los tokens del turno en
    cada chunk (O(n) por chunk, O(n²) por respuesta). Ahora solo une los
    tokens nuevos desde el chunk anterior y acumula el texto completo."""

    async def test_cada_chunk_solo_procesa_tokens_pendientes(self) -> None:
        # Chunks de 0,5 s (≈7 caracteres a 15 car/s): cada token de 4
        # caracteres cabe entero en su chunk (streaming más lento que la voz).
        engine, _, captured = _make_engine()
        for i in range(50):
            engine.push_token(f"t{i} ")
            await engine.play_chunk(_make_audio(0.5))
            # Los tokens ya publicados no se vuelven a recorrer.
            self.assertEqual(engine._text_buffer, [])
        events = [e for e in captured if e.type == "display_text_update"]
        self.assertEqual(events[-1].data["chunk_text"], "t49 ")
        self.assertEqual(events[-1].data["cumulative_text"],
                         "".join(f"t{i} " for i in range(50)))

    async def test_varios_tokens_entre_chunks(self) -> None:
        engine, _, captured = _make_engine()
        engine.push_token("a")
        engine.push_token("b")
        await engine.play_chunk(_make_audio(0.5))
        engine.push_token("c")
        engine.push_token("d")
        await engine.play_chunk(_make_audio(0.5))
        events = [e for e in captured if e.type == "display_text_update"]
        self.assertEqual([e.data["chunk_text"] for e in events], ["ab", "cd"])
        self.assertEqual([e.data["cumulative_text"] for e in events], ["ab", "abcd"])


# Texto de ~600 caracteres como el del log real del 2026-10-21: el gateway
# (con OpenClaw) entrega TODO el texto antes que el primer chunk de audio.
_RESPUESTA_LARGA = (
    "Claro, te pongo la lista de reproducción que me pides en Spotify. "
    "Mientras empieza, te cuento que esta mañana hará algo de frío en "
    "Madrid, con máximas de catorce grados y algunas nubes por la tarde, "
    "así que conviene llevar una chaqueta ligera si vas a salir. Para el "
    "fin de semana se esperan lluvias débiles el sábado y un domingo más "
    "despejado, ideal para dar un paseo por el Retiro o acercarte a la "
    "sierra. Si quieres, puedo recordarte mañana a primera hora que mires "
    "el tiempo antes de salir de casa, o añadir un aviso al calendario "
    "para la excursión del domingo. Dime qué prefieres y lo dejo listo."
)


class TestRitmoDelTexto(unittest.IsolatedAsyncioTestCase):
    """Prueba real 2026-10-21: con todo el texto delante del audio, el primer
    chunk (0,5 s) se llevaba los 600 caracteres y los 25 siguientes salían
    con chunk_text vacío. Ahora el texto pendiente se reparte al ritmo de
    habla estimado (chars_per_second), sin cortar palabras."""

    def _display(self, captured: list[VoiceEvent]) -> list[VoiceEvent]:
        return [e for e in captured if e.type == "display_text_update"]

    def _assert_invariante(self, events: list[VoiceEvent]) -> None:
        # cumulative_N == cumulative_{N-1} + chunk_text_N: display_http
        # calcula la base del reveal con len(cumulative) - len(chunk).
        previo = ""
        for e in events:
            self.assertEqual(e.data["cumulative_text"], previo + e.data["chunk_text"])
            previo = e.data["cumulative_text"]

    async def test_caso_real_todo_el_texto_antes_que_el_audio(self) -> None:
        engine, _, captured = _make_engine()
        palabras = _RESPUESTA_LARGA.split(" ")
        for palabra in palabras:
            engine.push_token(palabra + " ")
        texto = "".join(p + " " for p in palabras)
        for _ in range(26):
            await engine.play_chunk(_make_audio(0.5))
        await engine.drain()

        events = self._display(captured)
        self._assert_invariante(events)
        # Primer chunk: como mucho 0,5 s de habla más una palabra.
        cps = engine.chars_per_second
        palabra_max = max(len(p) for p in palabras) + 1
        self.assertGreater(len(events[0].data["chunk_text"]), 0)
        self.assertLessEqual(len(events[0].data["chunk_text"]), 0.5 * cps + palabra_max)
        # El texto se reparte entre los chunks de audio (antes: 25 de 26
        # vacíos). Alguno puede quedar vacío si el anterior terminó una
        # palabra larga y gastó su crédito.
        con_texto = sum(1 for e in events[:26] if e.data["chunk_text"])
        self.assertGreaterEqual(con_texto, 22)
        # Cortes siempre tras un espacio (sin partir palabras).
        for e in events:
            if e.data["chunk_text"]:
                self.assertTrue(e.data["chunk_text"].endswith(" "), repr(e.data["chunk_text"]))
        # Tras drain() el display tiene el texto completo.
        self.assertEqual(len(events), 27)
        self.assertEqual(events[-1].data["cumulative_text"], texto)
        self.assertEqual("".join(e.data["chunk_text"] for e in events), texto)

    async def test_ritmo_aproximado_a_chars_per_second(self) -> None:
        engine, _, captured = _make_engine()
        engine.push_token(_RESPUESTA_LARGA)
        for _ in range(20):  # 10 s de audio
            await engine.play_chunk(_make_audio(0.5))
        revelado = self._display(captured)[-1].data["cumulative_text"]
        esperado = 10 * engine.chars_per_second
        # El exceso por terminar palabra se descuenta (crédito): no se acumula.
        self.assertLess(abs(len(revelado) - esperado), 15)

    async def test_streaming_mas_lento_que_el_audio_se_comporta_como_antes(self) -> None:
        engine, _, captured = _make_engine()
        tokens = ["Mañana", " hará", " sol", " en", " Madrid."]
        for t in tokens:
            engine.push_token(t)
            await engine.play_chunk(_make_audio(0.6))
        await engine.drain()
        events = self._display(captured)
        self.assertEqual([e.data["chunk_text"] for e in events], tokens)
        self._assert_invariante(events)

    async def test_credito_sin_usar_no_se_acumula(self) -> None:
        """Audio sin texto al principio: no adelanta el texto que llegue luego."""
        engine, _, captured = _make_engine()
        for _ in range(10):  # 5 s de audio sin texto
            await engine.play_chunk(_make_audio(0.5))
        engine.push_token(_RESPUESTA_LARGA)
        await engine.play_chunk(_make_audio(0.5))
        ultimo = self._display(captured)[-1].data["chunk_text"]
        self.assertLessEqual(len(ultimo), 0.5 * engine.chars_per_second + 15)

    async def test_palabra_mas_larga_que_el_presupuesto_sale_entera(self) -> None:
        engine, _, captured = _make_engine(chars_per_second=2.0)
        engine.push_token("electroencefalografista dice hola")
        await engine.play_chunk(_make_audio(0.5))  # presupuesto: 1 carácter
        self.assertEqual(self._display(captured)[0].data["chunk_text"],
                         "electroencefalografista ")
        # El exceso se descuenta de los chunks siguientes.
        await engine.play_chunk(_make_audio(0.5))
        self.assertEqual(self._display(captured)[-1].data["chunk_text"], "")

    async def test_drain_vacia_el_resto_en_un_ultimo_text_update(self) -> None:
        engine, _, captured = _make_engine()
        engine.push_token(_RESPUESTA_LARGA)
        await engine.play_chunk(_make_audio(0.5))
        await engine.drain()
        events = self._display(captured)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1].data["cumulative_text"], _RESPUESTA_LARGA)
        self.assertEqual(events[-1].data["duration_s"], 0.0)
        self._assert_invariante(events)

    async def test_drain_publica_antes_de_esperar_al_backend(self) -> None:
        engine, _, captured = _make_engine()
        engine.push_token(_RESPUESTA_LARGA)
        await engine.play_chunk(_make_audio(0.5))
        vistos: list[int] = []

        async def _drain() -> None:
            vistos.append(len(self._display(captured)))

        engine._audio.drain.side_effect = _drain
        await engine.drain()
        self.assertEqual(vistos, [2])

    async def test_drain_sin_texto_pendiente_no_publica(self) -> None:
        engine, _, captured = _make_engine()
        engine.push_token("Hola")
        await engine.play_chunk(_make_audio(1.0))
        await engine.drain()
        self.assertEqual(len(self._display(captured)), 1)

    async def test_drain_publica_tokens_que_llegaron_sin_audio(self) -> None:
        """Turno que acaba solo en texto (el gateway descartó el TTS)."""
        engine, _, captured = _make_engine()
        engine.push_token("Solo texto")
        await engine.drain()
        events = self._display(captured)
        self.assertEqual([e.data["cumulative_text"] for e in events], ["Solo texto"])

    async def test_reset_descarta_texto_pendiente_y_credito(self) -> None:
        engine, _, captured = _make_engine(chars_per_second=2.0)
        engine.push_token("electroencefalografista anterior")
        await engine.play_chunk(_make_audio(0.5))  # deja crédito negativo
        engine.reset()
        captured.clear()
        engine.push_token("Nuevo")
        await engine.play_chunk(_make_audio(3.0))
        await engine.drain()
        events = self._display(captured)
        self.assertEqual([e.data["cumulative_text"] for e in events], ["Nuevo"])
