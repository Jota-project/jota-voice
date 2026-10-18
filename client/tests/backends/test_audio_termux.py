"""Tests offline del TermuxBackend.

No abre streams reales de PyAudio (no disponible en Mac/Linux); captura
los bytes que el backend entrega a `stream.write()` para validar la
lógica de alineamiento a muestras int16.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from unittest.mock import MagicMock, create_autospec

import pytest

from config import AudioConfig
from backends import audio_termux
from backends.audio_termux import TermuxBackend
from platforms.termux.audio_capture_termux import AudioCapture

from tests.fakes.audio_modules import PA_INT16, PyAudioSpec
from tests.fakes.waiting import wait_until


def _cfg(**kwargs) -> AudioConfig:
    base = dict(
        sample_rate=16000,
        channels=1,
        frames_per_buffer=512,
        preroll_seconds=1.5,
        vad_rms_threshold=200.0,
        input_device=None,
        output_device=None,
    )
    base.update(kwargs)
    return AudioConfig(**base)


class _FakeStream:
    """Captura los bytes que se pasan a `write()` para aserciones."""

    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes, num_frames: int | None = None) -> None:  # noqa: ARG002
        self.written.append(bytes(data))

    def stop_stream(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakePa:
    """Suficiente para que TermuxBackend crea que tiene un PyAudio."""

    paInt16 = 16

    def terminate(self) -> None:
        pass


def _backend_with_fake_stream() -> tuple[TermuxBackend, _FakeStream]:
    be = TermuxBackend(_cfg())
    be._pa = _FakePa()  # type: ignore[assignment]
    stream = _FakeStream()
    be._stream = stream  # type: ignore[assignment]
    return be, stream


def test_play_chunk_impar_no_pierde_bytes() -> None:
    """Fix de raíz: el chunking de red no garantiza alinear cada mensaje TTS
    a un número par de bytes (tamaño de una muestra int16). play_chunk debe
    arrastrar el byte suelto al siguiente chunk en vez de dejarlo pasar a
    PyAudio.Stream.write(), que internamente computa frames como
    len(data) // (channels * sample_width) y descarta silenciosamente el
    último byte si la longitud es impar — equivalente en la práctica al
    patrón `len(bytes) // 2` que perdía muestras en el backend Termux."""
    be, stream = _backend_with_fake_stream()

    async def _run():
        await be.play_chunk(bytes([1, 2, 3]))  # 3 bytes: impar
        await be.play_chunk(bytes([4, 5]))     # 2 bytes: par

    asyncio.run(_run())

    # Todo lo escrito a PyAudio debe tener longitud par (alineado a int16)
    # y la concatenación más el carry pendiente debe preservar todos los
    # bytes de entrada en orden, sin reordenamientos ni descarte.
    written_concat = b"".join(stream.written)
    assert all(len(c) % 2 == 0 for c in stream.written), (
        f"chunk impar escrito a PyAudio: {[len(c) for c in stream.written]}"
    )
    assert written_concat + be._enqueue_carry == bytes([1, 2, 3, 4, 5])


def test_play_notification_no_pierde_bytes() -> None:
    """El beep sintetizado se genera como int16 (longitud par por
    construcción), pero el path debe pasar por la misma lógica de
    alineamiento por simetría y para no tener dos rutas divergentes."""
    be, stream = _backend_with_fake_stream()

    asyncio.run(be.play_notification())

    assert len(stream.written) >= 1
    assert all(len(c) % 2 == 0 for c in stream.written), (
        f"chunk impar escrito a PyAudio: {[len(c) for c in stream.written]}"
    )


def test_drain_resets_carry() -> None:
    """Tras drain() el byte suelto pendiente se descarta: ya no hay un
    siguiente chunk al que pegárselo y forzar su reproducción causaría un
    sample arbitrario al final del turno."""
    be, _ = _backend_with_fake_stream()
    be._enqueue_carry = bytes([0xAB])

    asyncio.run(be.drain())

    assert be._enqueue_carry == b""


def test_stop_resets_carry() -> None:
    """Igual que drain: stop() debe limpiar el estado de carry para que
    un nuevo start() no empiece arrastrando bytes de la sesión anterior."""
    be, _ = _backend_with_fake_stream()
    be._enqueue_carry = bytes([0xAB])

    asyncio.run(be.stop())

    assert be._enqueue_carry == b""


def test_reset_resets_carry() -> None:
    """reset() lo llama PlaybackEngine.reset() entre turnos; debe limpiar
    el carry para que un byte suelto de un turno cancelado a medias no se
    cuele en el siguiente turno como ruido audible."""
    be, _ = _backend_with_fake_stream()
    be._enqueue_carry = bytes([0xAB])

    be.reset()

    assert be._enqueue_carry == b""


def test_reset_clears_preroll() -> None:
    """Reset debe limpiar también el preroll (audio pre-wake-word capturado
    antes de que se cancelara el turno), para que no se cuele en la
    siguiente transcripción. Asimetría con SounddeviceBackend.reset() que
    sí lo hace — ver hallazgo de revisión post-Fase A."""
    be, _ = _backend_with_fake_stream()
    be._capture._preroll.append(b"stale-audio-from-prev-turn")

    be.reset()

    assert be.get_preroll() == b""


def test_play_chunk_un_solo_byte_se_acumula_en_carry() -> None:
    """Un chunk de 1 byte no produce ninguna muestra completa: el byte
    debe quedar en carry sin escribirse a PyAudio, y un chunk posterior
    de 1 byte más debe completarlo como una muestra int16."""
    be, stream = _backend_with_fake_stream()

    async def _run():
        await be.play_chunk(bytes([0x42]))          # 1 byte: nada escribible
        assert stream.written == []
        assert be._enqueue_carry == bytes([0x42])
        await be.play_chunk(bytes([0xCD]))          # completa la muestra

    asyncio.run(_run())

    assert b"".join(stream.written) == bytes([0x42, 0xCD])
    assert be._enqueue_carry == b""


def test_play_chunk_par_luego_impar() -> None:
    """Inverso del test principal: si el primer chunk ya está alineado,
    no debe quedar nada en carry y el chunk impar siguiente arrastra
    su byte suelto al subsiguiente."""
    be, stream = _backend_with_fake_stream()

    async def _run():
        await be.play_chunk(bytes([1, 2]))      # par → escribe, carry=""
        await be.play_chunk(bytes([3]))         # impar → carry=[3]
        await be.play_chunk(bytes([4, 5, 6]))   # 3+3=6 par → escribe [3,4,5,6]

    asyncio.run(_run())

    assert b"".join(stream.written) == bytes([1, 2, 3, 4, 5, 6])
    assert be._enqueue_carry == b""


def test_play_chunk_vacio_con_carry_pendiente_no_escribe() -> None:
    """Una llamada vacía (b"") debe ser no-op incluso con carry pendiente:
    ni escribe ni descarta el byte suelto (otra llamada posterior podría
    completarlo)."""
    be, stream = _backend_with_fake_stream()
    be._enqueue_carry = bytes([0xAB])

    asyncio.run(be.play_chunk(b""))

    assert stream.written == []
    assert be._enqueue_carry == bytes([0xAB])


def test_stop_no_close_stream_during_concurrent_play() -> None:
    """Regresión #29: stop() debe adquirir self._lock para serializar con
    _play() y drain().

    Escenario real de fallo: _play() entra en `async with self._lock` y
    delega en `run_in_executor(None, self._stream.write, audio)`. Si una
    coroutine de shutdown llama stop() concurrentemente y stop() no adquiere
    el lock, puede ejecutar self._stream.stop_stream() / self._stream.close()
    en paralelo mientras write() aún corre, o dejar self._stream = None
    mientras otro task está en `run_in_executor(None, self._stream.write, ...)`.
    En PyAudio real esto se manifiesta como excepciones tipo "Stream closed"
    / "Invalid sample rate" en write() / write retorna contra un handle
    inválido.

    Test minimalista: simula el race con un write() bloqueante y verifica
    que stop() no haya podido cerrar el stream mientras _play() seguía
    dentro del lock. Sin el fix, stop() cierra el stream a la primera
    (no respeta lock) y el assert falla; con el fix, stop() espera a que
    _play() libere el lock antes de cerrar.
    """
    be, stream = _backend_with_fake_stream()
    # Fixture: _stream existe, así el lock de _play() se mantiene mientras
    # write() (bloqueante) corre en el executor.

    write_entered = threading.Event()
    release_write = threading.Event()

    original_write = stream.write

    def blocking_write(data: bytes, num_frames: int | None = None) -> None:  # noqa: ARG001
        write_entered.set()
        # Bloquea hasta que el test lo libere: simula un write() lento
        # (por ejemplo, PortAudio con buffer alto bajo carga).
        assert release_write.wait(timeout=5.0), "timeout esperando liberación de write"
        return original_write(data, num_frames)

    stream.write = blocking_write  # type: ignore[method-assign]

    async def _run() -> None:
        # _play() toma self._lock y entra en run_in_executor con write() bloqueado.
        play_task = asyncio.create_task(be.play_chunk(bytes([1, 2])))
        await asyncio.get_running_loop().run_in_executor(
            None, write_entered.wait, 5.0
        )
        # _play() ahora está dentro de write() con self._lock retenido.
        # Lanzar stop() concurrentemente: si no adquiere el lock, cerrará
        # el stream a la primera.
        stop_task = asyncio.create_task(be.stop())
        # Esperar a que stop() quede bloqueado en el lock (o termine, si no
        # lo adquiere) en vez de dormir 0.05s y confiar en que avance
        # (issue #76): con la CPU cargada el sleep no probaba nada.
        await wait_until(
            lambda: stop_task.done() or bool(getattr(be._lock, "_waiters", None)),
            message="stop() bloqueado en el lock o terminado",
        )
        assert be._stream is not None, (
            "stop() cerró el stream mientras _play() estaba en write(). "
            "stop() debe adquirir self._lock para serializar con _play()/drain() "
            "(issue #29)."
        )
        # Liberar write() → _play() suelta lock → stop() puede cerrar.
        release_write.set()
        await asyncio.gather(stop_task, play_task)
        # Después de que stop() termina con lock adquirido, el stream sí está cerrado.
        assert be._stream is None
        assert be._pa is None

    asyncio.run(_run())


def test_stop_idempotent() -> None:
    """stop() debe ser idempotente: una segunda llamada no debe romper
    aunque self._stream y self._pa ya sean None."""
    be, _ = _backend_with_fake_stream()

    async def _run() -> None:
        await be.stop()
        await be.stop()  # segunda vez: no-op seguro

    asyncio.run(_run())

    assert be._stream is None
    assert be._pa is None


# ---------------------------------------------------------------------------
# Issue #35 — huecos de cobertura: import lazy de pyaudio, start(), apertura
# perezosa del stream de salida, cierre con excepciones y delegación en
# AudioCapture. Sin PyAudio ni parec reales: pyaudio se stubbea con los
# fixtures fake_pyaudio/no_pyaudio de tests/conftest.py y AudioCapture se
# sustituye por un mock con autospec.
# ---------------------------------------------------------------------------


def _backend_with_mock_capture() -> tuple[TermuxBackend, MagicMock]:
    be = TermuxBackend(_cfg())
    capture = create_autospec(AudioCapture, instance=True)
    be._capture = capture  # type: ignore[assignment]
    return be, capture


def test_require_pyaudio_sin_modulo_lanza_runtimeerror_con_instrucciones(no_pyaudio) -> None:
    """Fuera de Termux pyaudio no existe: el error debe ser explícito y
    decir cómo instalarlo, encadenando el ImportError original."""
    with pytest.raises(RuntimeError, match="pkg install portaudio") as excinfo:
        audio_termux._require_pyaudio()

    assert isinstance(excinfo.value.__cause__, ImportError)


def test_construir_backend_no_importa_pyaudio(no_pyaudio) -> None:
    """El import es lazy: instanciar el backend (p. ej. desde el registry
    en una plataforma sin pyaudio) no debe fallar."""
    be = TermuxBackend(_cfg())

    assert be._pa is None
    assert be._stream is None


def test_start_arranca_captura_e_inicializa_pyaudio(fake_pyaudio) -> None:
    pa_cls, pa_instance = fake_pyaudio.cls, fake_pyaudio.instance
    be, capture = _backend_with_mock_capture()

    asyncio.run(be.start())

    capture.start.assert_awaited_once_with()
    pa_cls.assert_called_once_with()
    assert be._pa is pa_instance
    # El stream de salida NO se abre en start(): es perezoso.
    pa_instance.open.assert_not_called()
    assert be._stream is None


def test_start_sin_pyaudio_propaga_runtimeerror(no_pyaudio) -> None:
    """Bug de la Fase 4c: start() arrancaba parec y su hilo lector antes de
    importar pyaudio; si el import fallaba, la captura quedaba viva y nadie
    la paraba. pyaudio se comprueba antes de tocar el micrófono."""
    be, capture = _backend_with_mock_capture()

    with pytest.raises(RuntimeError, match="pyaudio"):
        asyncio.run(be.start())

    assert be._pa is None
    capture.start.assert_not_awaited()


def test_start_para_la_captura_si_pyaudio_no_inicializa(fake_pyaudio) -> None:
    """Si PyAudio() lanza (PortAudio sin dispositivos, p. ej.) con la
    captura ya arrancada, start() la para antes de propagar el error."""
    fake_pyaudio.cls.side_effect = OSError("no default output device")
    be, capture = _backend_with_mock_capture()

    with pytest.raises(OSError, match="no default output device"):
        asyncio.run(be.start())

    capture.start.assert_awaited_once_with()
    capture.stop.assert_awaited_once_with()
    assert be._pa is None


def test_play_abre_stream_perezoso_una_sola_vez_con_formato_tts(fake_pyaudio) -> None:
    """El primer chunk abre el stream de salida (PCM16 mono 24 kHz, el
    formato TTS del gateway); los siguientes reutilizan el mismo."""
    pa_instance = fake_pyaudio.instance
    be, _ = _backend_with_mock_capture()

    async def _run() -> None:
        await be.start()
        await be.play_chunk(bytes([1, 2]))
        await be.play_chunk(bytes([3, 4]))

    asyncio.run(_run())

    pa_instance.open.assert_called_once_with(
        format=PA_INT16, channels=1, rate=24000, output=True
    )
    stream = be._stream
    assert stream is not None
    assert [c.args[0] for c in stream.write.call_args_list] == [bytes([1, 2]), bytes([3, 4])]


def test_play_antes_de_start_es_noop() -> None:
    """Sin PyAudio inicializado (_pa None) play_chunk no debe abrir nada
    ni lanzar: el audio simplemente se descarta."""
    be, _ = _backend_with_mock_capture()

    asyncio.run(be.play_chunk(bytes([1, 2, 3, 4])))

    assert be._stream is None
    assert be._enqueue_carry == b""


def test_stop_para_captura_cierra_stream_y_termina_pyaudio() -> None:
    be, capture = _backend_with_mock_capture()
    pa = create_autospec(PyAudioSpec, instance=True)
    stream = create_autospec(_FakeStream, instance=True)
    be._pa = pa  # type: ignore[assignment]
    be._stream = stream  # type: ignore[assignment]

    asyncio.run(be.stop())

    capture.stop.assert_awaited_once_with()
    stream.stop_stream.assert_called_once_with()
    stream.close.assert_called_once_with()
    pa.terminate.assert_called_once_with()
    assert be._stream is None
    assert be._pa is None


def test_stop_tolera_excepcion_al_cerrar_stream() -> None:
    """Un stream ya roto (p. ej. dispositivo desaparecido) no debe impedir
    terminar PyAudio ni dejar referencias colgando."""
    be, _ = _backend_with_mock_capture()
    pa = create_autospec(PyAudioSpec, instance=True)
    stream = create_autospec(_FakeStream, instance=True)
    stream.close.side_effect = OSError("Stream closed")
    be._pa = pa  # type: ignore[assignment]
    be._stream = stream  # type: ignore[assignment]

    asyncio.run(be.stop())

    pa.terminate.assert_called_once_with()
    assert be._stream is None
    assert be._pa is None


def test_drain_cierra_stream_y_el_siguiente_play_abre_otro(fake_pyaudio) -> None:
    pa_instance = fake_pyaudio.instance
    be, _ = _backend_with_mock_capture()

    async def _run() -> tuple[MagicMock, MagicMock]:
        await be.start()
        await be.play_chunk(bytes([1, 2]))
        first = be._stream
        await be.drain()
        assert be._stream is None
        await be.play_chunk(bytes([3, 4]))
        return first, be._stream

    first, second = asyncio.run(_run())

    first.stop_stream.assert_called_once_with()
    first.close.assert_called_once_with()
    assert second is not None and second is not first
    assert pa_instance.open.call_count == 2


def test_drain_error_cerrando_stream_loguea_y_limpia(caplog) -> None:
    be, _ = _backend_with_mock_capture()
    be._pa = create_autospec(PyAudioSpec, instance=True)  # type: ignore[assignment]
    stream = create_autospec(_FakeStream, instance=True)
    stream.stop_stream.side_effect = OSError("boom")
    be._stream = stream  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING, logger="backends.audio_termux"):
        asyncio.run(be.drain())

    assert be._stream is None
    assert any("error cerrando stream" in r.getMessage() for r in caplog.records)


def test_drain_sin_stream_es_noop() -> None:
    be, _ = _backend_with_mock_capture()

    asyncio.run(be.drain())

    assert be._stream is None


def test_play_drain_stop_concurrentes_no_hacen_deadlock(fake_pyaudio) -> None:
    """El asyncio.Lock compartido por _play/drain/stop no debe bloquearse
    (p. ej. por adquisición reentrante) cuando se intercalan."""
    be, capture = _backend_with_mock_capture()

    async def _run() -> None:
        await be.start()
        await asyncio.wait_for(
            asyncio.gather(
                be.play_chunk(bytes([1, 2])),
                be.drain(),
                be.play_chunk(bytes([3, 4])),
                be.play_notification(),
                be.stop(),
            ),
            timeout=2.0,
        )

    asyncio.run(_run())

    capture.stop.assert_awaited_once_with()
    assert be._pa is None
    assert be._stream is None


def test_accesores_delegan_en_audio_capture() -> None:
    be, capture = _backend_with_mock_capture()
    q: asyncio.Queue[bytes] = asyncio.Queue()
    oww_q: asyncio.Queue[bytes] = asyncio.Queue()
    capture.get_queue.return_value = q
    capture.get_oww_queue.return_value = oww_q
    capture.get_preroll.return_value = b"preroll"
    capture.is_silence.return_value = True

    assert be.get_queue() is q
    assert be.get_oww_queue() is oww_q
    assert be.get_preroll() == b"preroll"
    assert be.is_silence(b"\x00\x00") is True
    capture.is_silence.assert_called_once_with(b"\x00\x00")


def test_reset_delega_en_audio_capture() -> None:
    be, capture = _backend_with_mock_capture()

    be.reset()

    capture.reset.assert_called_once_with()
