"""Tests offline del SounddeviceBackend (VAD + preroll + ring-buffer).

No abre streams reales (no requiere hardware); solo valida la lógica de
is_silence() y get_preroll() con frames sintéticos.
"""
from __future__ import annotations

import asyncio
import queue as queue_mod
import threading
from unittest.mock import create_autospec

import numpy as np
import pytest

import sounddevice as sd

from config import AudioConfig
from backends.audio_sounddevice import SounddeviceBackend


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


def _silence_frame(samples: int = 512) -> bytes:
    return np.zeros(samples, dtype=np.float32).tobytes()


def _tone_frame(samples: int = 512, amplitude: float = 0.5) -> bytes:
    t = np.arange(samples) / 16000.0
    wave = (np.sin(2 * np.pi * 440.0 * t) * amplitude).astype(np.float32)
    return wave.tobytes()


def test_is_silence_silent_frame() -> None:
    be = SounddeviceBackend(_cfg())
    assert be.is_silence(_silence_frame()) is True


def test_is_silence_loud_frame() -> None:
    be = SounddeviceBackend(_cfg())
    assert be.is_silence(_tone_frame(amplitude=0.5)) is False


def test_get_preroll_empty_at_init() -> None:
    be = SounddeviceBackend(_cfg())
    assert be.get_preroll() == b""


def test_get_preroll_returns_recent_frames() -> None:
    be = SounddeviceBackend(_cfg())
    # Inyectar 10 frames en el ring-buffer interno (sin abrir stream)
    for _ in range(10):
        be._preroll.append(_tone_frame(amplitude=0.1))
    preroll = be.get_preroll()
    assert len(preroll) == 10 * 512 * 4  # 10 frames × 512 samples × 4 bytes (float32)


def test_preroll_maxlen_respects_seconds() -> None:
    cfg = _cfg(frames_per_buffer=512, sample_rate=16000, preroll_seconds=1.0)
    be = SounddeviceBackend(cfg)
    # maxlen = 1.0 * 16000 / 512 = ~31 frames
    assert be._preroll.maxlen == 31


def test_reset_clears_preroll() -> None:
    be = SounddeviceBackend(_cfg())
    be._preroll.append(_tone_frame())
    be.reset()
    assert be.get_preroll() == b""


def test_out_callback_cola_vacia_rellena_silencio() -> None:
    """Bug: outdata[:] = bytes crudos lanzaba ValueError en vez de escribir silencio."""
    be = SounddeviceBackend(_cfg())
    frames = 8
    outdata = np.ones((frames, 1), dtype=np.int16)
    be._out_callback(outdata, frames, None, None)  # no debe lanzar
    assert np.all(outdata == 0)


def test_out_callback_chunk_impar_no_lanza() -> None:
    """Bug real (2026-07-09): un chunk TTS de longitud impar de bytes hacía
    que np.frombuffer(..., dtype=np.int16) lanzara ValueError dentro del
    callback de PortAudio. sounddevice registra ese callback con
    error=paAbort, así que una excepción sin capturar aborta el
    OutputStream ENTERO — no solo ese chunk — dejando el resto de la
    respuesta en silencio permanente sin ningún error visible."""
    be = SounddeviceBackend(_cfg())
    frames = 8
    chunk = bytes([1, 2, 3])  # 3 bytes: no es múltiplo de 2 (tamaño de int16)
    be._play_q.put(chunk)

    outdata = np.zeros((frames, 1), dtype=np.int16)
    be._out_callback(outdata, frames, None, None)  # no debe lanzar


def test_out_callback_no_trunca_chunk_mayor_que_frames() -> None:
    """Bug: un chunk TTS más grande que `frames` perdía silenciosamente el resto."""
    be = SounddeviceBackend(_cfg())
    frames = 4
    chunk = np.arange(1, 11, dtype=np.int16).tobytes()  # 10 samples, > frames
    be._play_q.put(chunk)

    outdata1 = np.zeros((frames, 1), dtype=np.int16)
    be._out_callback(outdata1, frames, None, None)
    assert list(outdata1.ravel()) == [1, 2, 3, 4]

    outdata2 = np.zeros((frames, 1), dtype=np.int16)
    be._out_callback(outdata2, frames, None, None)
    assert list(outdata2.ravel()) == [5, 6, 7, 8]

    outdata3 = np.zeros((frames, 1), dtype=np.int16)
    be._out_callback(outdata3, frames, None, None)
    assert list(outdata3.ravel()) == [9, 10, 0, 0]


def test_play_chunk_nunca_encola_bytes_impares() -> None:
    """Fix de raíz: el chunking de red no garantiza alinear cada mensaje a
    un número par de bytes (tamaño de una muestra int16). play_chunk debe
    arrastrar el byte suelto al siguiente chunk en vez de dejar que un
    chunk de longitud impar llegue a _play_q (ver test_out_callback_chunk_impar_no_lanza)."""
    be = SounddeviceBackend(_cfg(output_device=None))
    be._ensure_output_stream = lambda: None  # evita abrir hardware real

    async def _run():
        await be.play_chunk(bytes([1, 2, 3]))  # 3 bytes: impar
        await be.play_chunk(bytes([4, 5]))     # 2 bytes: par

    asyncio.run(_run())

    queued = []
    while not be._play_q.empty():
        queued.append(be._play_q.get_nowait())

    assert all(len(c) % 2 == 0 for c in queued), f"chunk impar encolado: {queued}"
    # El byte 5 final queda pendiente como carry (aún no tiene pareja para
    # formar una muestra int16 completa) — no se pierde, ninguno de los
    # bytes 1-4 se reordena ni se descarta.
    assert b"".join(queued) == bytes([1, 2, 3, 4])
    assert be._enqueue_carry == bytes([5])


def test_enqueue_and_wait_queues_audio_before_starting_stream() -> None:
    """Bug: _ensure_output_stream() (que llama a stream.start(), arrancando
    el callback de PortAudio en su hilo de tiempo real) se invocaba antes de
    encolar el audio en _play_q. Esto deja una ventana de carrera: el
    callback puede pedir datos justo al arrancar el stream y encontrar la
    cola vacía, reproduciendo silencio y perdiendo el principio del audio
    (un click al inicio de cada beep/respuesta). El audio debe estar en la
    cola ANTES de arrancar el stream."""
    be = SounddeviceBackend(_cfg())
    qsize_at_ensure = []

    def fake_ensure_output_stream() -> None:
        qsize_at_ensure.append(be._play_q.qsize())

    be._ensure_output_stream = fake_ensure_output_stream

    asyncio.run(be.play_chunk(bytes([1, 2, 3, 4])))

    assert qsize_at_ensure == [1]


def test_enqueue_and_wait_sleeps_solo_la_duracion_real(monkeypatch) -> None:
    """Bug: se dormía duration + 0.05s tras encolar cada chunk. Como el
    callback de PortAudio consume el chunk en su duración real (sin ese
    margen extra), esos 50ms de más hacían que el productor entregara el
    siguiente chunk más tarde de lo que el hardware tardaba en consumir el
    anterior — la cola se vaciaba y el callback rellenaba con silencio real
    entre chunk y chunk (huecos audibles, no distorsión)."""
    be = SounddeviceBackend(_cfg())
    be._ensure_output_stream = lambda: None
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    import backends.audio_sounddevice as mod

    # mod.asyncio ES el módulo asyncio global: parchearlo a mano con
    # try/finally afectaba a todo el proceso; monkeypatch lo acota al test.
    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
    audio = bytes(range(1, 1 + 240))  # 120 samples int16 → 120/24000s
    asyncio.run(be.play_chunk(audio))

    expected_duration = len(audio) / (24000 * 2)
    assert slept == [pytest.approx(expected_duration)]


# --- issue #25: start() debe reintentar ante PortAudioError transitorio ---
#
# El módulo sounddevice se sustituye con el fixture fake_sounddevice
# (tests/conftest.py): spec del módulo real y PortAudioError real.


def _no_wait_sleep(monkeypatch):
    """Sustituye asyncio.sleep en el módulo de audio para no esperar
    realmente entre reintentos (los 0.5+1.0+2.0=3.5s harían el test lento)."""
    import backends.audio_sounddevice as mod

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)


def test_start_retries_on_portaudio_error_then_succeeds(monkeypatch, fake_sounddevice) -> None:
    """Issue #25: si sd.InputStream() lanza PortAudioError en los primeros
    intentos (p.ej. 'device busy', host API no lista, dispositivo recién
    desconectado), start() debe reintentar hasta tener éxito y NO dejar al
    cliente sin captura."""
    attempts = {"n": 0}
    successful_stream = create_autospec(sd.InputStream, instance=True)

    def make_input_stream(**_kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise sd.PortAudioError(f"transient (attempt {attempts['n']})")
        return successful_stream

    fake_sounddevice.InputStream.side_effect = make_input_stream
    _no_wait_sleep(monkeypatch)

    be = SounddeviceBackend(_cfg())
    asyncio.run(be.start())  # no debe lanzar

    assert attempts["n"] == 3, f"esperaba 3 intentos, vi {attempts['n']}"
    assert be._input_stream is successful_stream
    successful_stream.start.assert_called_once()


def test_start_raises_after_all_retries_exhausted(monkeypatch, fake_sounddevice) -> None:
    """Si TODOS los intentos fallan, la última PortAudioError se propaga al
    llamador para que el usuario vea el problema en vez de quedarse en
    idle perpetuo sin señal."""
    attempts = {"n": 0}

    def make_input_stream(**_kwargs):
        attempts["n"] += 1
        raise sd.PortAudioError(f"always fails (attempt {attempts['n']})")

    fake_sounddevice.InputStream.side_effect = make_input_stream
    _no_wait_sleep(monkeypatch)

    be = SounddeviceBackend(_cfg())
    with pytest.raises(sd.PortAudioError, match="always fails"):
        asyncio.run(be.start())

    # 3 = longitud del start_retry_backoff_s default ([0.5, 1.0, 2.0])
    assert attempts["n"] == 3, f"esperaba 3 intentos, vi {attempts['n']}"


def test_start_succeeds_on_first_try_normal(monkeypatch, fake_sounddevice) -> None:
    """No regresión: si NO hay error, start() debe abrir el stream en el
    primer intento sin reintentos espurios."""
    attempts = {"n": 0}
    successful_stream = create_autospec(sd.InputStream, instance=True)

    def make_input_stream(**_kwargs):
        attempts["n"] += 1
        return successful_stream

    fake_sounddevice.InputStream.side_effect = make_input_stream
    _no_wait_sleep(monkeypatch)

    be = SounddeviceBackend(_cfg())
    asyncio.run(be.start())

    assert attempts["n"] == 1, f"sin error no debería reintentar (vi {attempts['n']})"
    assert be._input_stream is successful_stream


# --- issue #88: _play_leftover compartido con el hilo realtime de PortAudio ---


class _BlockingQueue:
    """Cola falsa: get_nowait() entrega un chunk y se queda parada hasta que
    el test la suelte, para abrir la ventana entre "el callback ya sacó el
    chunk" y "el callback escribe _play_leftover"."""

    def __init__(self, chunk: bytes) -> None:
        self._chunk: bytes | None = chunk
        self.inside = threading.Event()
        self.proceed = threading.Event()

    def get_nowait(self) -> bytes:
        if self._chunk is None:
            raise queue_mod.Empty
        chunk, self._chunk = self._chunk, None
        self.inside.set()
        assert self.proceed.wait(5.0), "el test no soltó la cola"
        return chunk

    def empty(self) -> bool:
        return self._chunk is None

    def put(self, chunk: bytes) -> None:  # pragma: no cover - no se usa
        self._chunk = chunk


class _ObservedLock:
    """Lock real con un aviso: ``trying`` se activa cuando alguien entra con
    ``with`` (espera bloqueante, el lado de asyncio). El callback usa
    ``acquire(blocking=False)``, que va directo al lock real."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.trying = threading.Event()

    def acquire(self, blocking: bool = True) -> bool:
        return self._lock.acquire(blocking)

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> "_ObservedLock":
        self.trying.set()
        self._lock.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self._lock.release()


def test_reset_no_deja_leftover_viejo_tras_callback_concurrente() -> None:
    """Issue #88: el callback saca un chunk más grande que el bloque, reset()
    limpia todo en otro hilo y después el callback guardaba el resto en
    _play_leftover: ese audio viejo sonaba al principio del turno siguiente
    (p. ej. tras interrumpir el TTS con la wake word)."""
    be = SounddeviceBackend(_cfg())
    lock = _ObservedLock()
    be._play_lock = lock  # type: ignore[assignment]
    frames = 4
    q = _BlockingQueue(np.arange(1, 11, dtype=np.int16).tobytes())  # 10 > 4
    be._play_q = q  # type: ignore[assignment]

    outdata = np.zeros((frames, 1), dtype=np.int16)
    cb = threading.Thread(target=be._out_callback, args=(outdata, frames, None, None))
    cb.start()
    resetter = threading.Thread(target=be.reset)
    try:
        assert q.inside.wait(5.0)
        resetter.start()
        # reset() tiene que pedir el lock de playback y quedarse esperando
        # mientras el callback está a mitad de bloque (sin el lock, reset()
        # vaciaba todo y terminaba aquí mismo).
        assert lock.trying.wait(5.0), "reset() no usa el lock de playback"
        assert resetter.is_alive(), "reset() mutó el estado de playback a mitad del callback"
    finally:
        q.proceed.set()
        cb.join(5.0)
        if resetter.ident is not None:  # join() de un hilo no arrancado lanza
            resetter.join(5.0)
    assert not cb.is_alive() and not resetter.is_alive()

    assert be._play_leftover is None, "audio del turno anterior sobrevivió a reset()"


def test_out_callback_no_bloquea_si_reset_tiene_el_lock() -> None:
    """El callback realtime nunca espera un lock: si el hilo de asyncio está
    limpiando la cola, ese bloque sale en silencio y el callback vuelve."""
    be = SounddeviceBackend(_cfg())
    be._play_q.put(np.arange(1, 5, dtype=np.int16).tobytes())
    frames = 4
    outdata = np.ones((frames, 1), dtype=np.int16)

    with be._play_lock:
        done = threading.Event()

        def _run() -> None:
            be._out_callback(outdata, frames, None, None)
            done.set()

        t = threading.Thread(target=_run)
        t.start()
        assert done.wait(5.0), "el callback realtime se quedó esperando el lock"
        t.join(5.0)

    assert np.all(outdata == 0)
    # El chunk no se consumió: sonará en el siguiente bloque.
    assert be._play_q.qsize() == 1


def test_out_callback_no_usa_el_lock_del_preroll() -> None:
    """El hilo de captura toma self._lock en cada frame; si el callback de
    salida compartiera ese lock perdería bloques (silencio a mitad del TTS)."""
    be = SounddeviceBackend(_cfg())
    be._play_q.put(np.arange(1, 5, dtype=np.int16).tobytes())
    frames = 4
    outdata = np.zeros((frames, 1), dtype=np.int16)

    with be._lock:
        be._out_callback(outdata, frames, None, None)

    assert list(outdata.ravel()) == [1, 2, 3, 4]


# --- issue #87: stop() no debe bloquear el loop de asyncio ---------------
#
# Y, de paso, fallos parciales: ningún camino de error deja un stream de
# PortAudio abierto.


def _stream_mock(cls=None):
    return create_autospec(cls or sd.InputStream, instance=True)


def test_stop_cierra_los_streams_fuera_del_hilo_del_loop() -> None:
    """Issue #87: stop()/close() de PortAudio pueden colgarse (Bluetooth que
    se desconecta a media reproducción); no pueden correr en el hilo del loop."""
    be = SounddeviceBackend(_cfg())
    threads: list[threading.Thread] = []
    inp = _stream_mock()
    out = _stream_mock(sd.OutputStream)
    for s in (inp, out):
        s.stop.side_effect = lambda: threads.append(threading.current_thread())
        s.close.side_effect = lambda: threads.append(threading.current_thread())
    be._input_stream = inp
    be._output_stream = out

    async def _run() -> threading.Thread:
        await be.stop()
        return threading.current_thread()

    loop_thread = asyncio.run(_run())

    for s in (inp, out):
        s.stop.assert_called_once_with()
        s.close.assert_called_once_with()
    assert len(threads) == 4
    assert all(t is not loop_thread for t in threads)
    assert be._input_stream is None and be._output_stream is None


def test_stop_cierra_aunque_stop_del_stream_falle() -> None:
    """Antes, si input.stop() lanzaba, input.close() no se llamaba nunca."""
    be = SounddeviceBackend(_cfg())
    inp = _stream_mock()
    inp.stop.side_effect = sd.PortAudioError("device gone")
    out = _stream_mock(sd.OutputStream)
    out.stop.side_effect = sd.PortAudioError("device gone")
    be._input_stream = inp
    be._output_stream = out

    asyncio.run(be.stop())

    inp.close.assert_called_once_with()
    out.close.assert_called_once_with()
    assert be._input_stream is None and be._output_stream is None


def test_stop_no_se_cuelga_si_el_driver_no_vuelve(monkeypatch, caplog) -> None:
    """Un driver colgado no debe impedir el apagado: stop() se rinde tras un
    timeout acotado, lo loguea y sigue con el resto."""
    import backends.audio_sounddevice as mod

    monkeypatch.setattr(mod, "_STREAM_CLOSE_TIMEOUT_S", 0.01)
    be = SounddeviceBackend(_cfg())
    release = threading.Event()
    inp = _stream_mock()
    inp.stop.side_effect = lambda: release.wait(5.0)
    out = _stream_mock(sd.OutputStream)
    be._input_stream = inp
    be._output_stream = out
    be._play_q.put(b"\x00\x00")

    try:
        with caplog.at_level("WARNING", logger=mod.__name__):
            asyncio.run(asyncio.wait_for(be.stop(), timeout=5.0))
    finally:
        release.set()

    # El stream colgado se abandona: su hilo sigue dentro de PortAudio y un
    # close() concurrente sobre el mismo stream no es seguro.
    inp.close.assert_not_called()
    out.stop.assert_called_once_with()
    out.close.assert_called_once_with()
    assert be._play_q.empty()
    assert any("timeout" in r.getMessage().lower() for r in caplog.records)


def test_drain_soporta_stop_concurrente() -> None:
    """drain() leía self._output_stream tras cada await: un stop() concurrente
    lo ponía a None entre medias (AttributeError sobre None)."""
    be = SounddeviceBackend(_cfg())
    out = _stream_mock(sd.OutputStream)
    be._output_stream = out

    async def _run() -> None:
        await asyncio.gather(be.drain(), be.stop())

    asyncio.run(_run())

    out.stop.assert_called_once_with()
    out.close.assert_called_once_with()
    assert be._output_stream is None


def test_start_cierra_el_stream_si_start_del_stream_falla(monkeypatch, fake_sounddevice) -> None:
    """Fallo parcial: InputStream() se crea pero .start() lanza. Cada reintento
    dejaba un stream de PortAudio abierto (el dispositivo seguía ocupado)."""
    streams: list = []

    def make_input_stream(**_kwargs):
        s = _stream_mock()
        if len(streams) < 2:
            s.start.side_effect = sd.PortAudioError("busy")
        streams.append(s)
        return s

    fake_sounddevice.InputStream.side_effect = make_input_stream
    _no_wait_sleep(monkeypatch)

    be = SounddeviceBackend(_cfg())
    asyncio.run(be.start())

    assert len(streams) == 3
    for failed in streams[:2]:
        failed.close.assert_called_once_with()
    streams[2].close.assert_not_called()
    assert be._input_stream is streams[2]


def test_start_sin_backoff_cierra_el_stream_si_start_falla(fake_sounddevice) -> None:
    s = _stream_mock()
    s.start.side_effect = sd.PortAudioError("busy")
    fake_sounddevice.InputStream.return_value = s

    be = SounddeviceBackend(_cfg(start_retry_backoff_s=[]))
    with pytest.raises(sd.PortAudioError):
        asyncio.run(be.start())

    s.close.assert_called_once_with()
    assert be._input_stream is None


def test_ensure_output_stream_no_guarda_un_stream_que_no_arranco(fake_sounddevice) -> None:
    """Fallo parcial en la salida: se asignaba self._output_stream antes de
    .start(); si start fallaba, las llamadas siguientes veían "ya hay stream"
    y la reproducción quedaba en silencio para siempre."""
    broken = _stream_mock(sd.OutputStream)
    broken.start.side_effect = sd.PortAudioError("no output device")
    good = _stream_mock(sd.OutputStream)
    fake_sounddevice.OutputStream.side_effect = [broken, good]

    be = SounddeviceBackend(_cfg())
    with pytest.raises(sd.PortAudioError):
        be._ensure_output_stream()

    broken.close.assert_called_once_with()
    assert be._output_stream is None

    be._ensure_output_stream()
    assert be._output_stream is good
    good.start.assert_called_once_with()
