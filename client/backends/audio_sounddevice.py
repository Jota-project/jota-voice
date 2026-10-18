"""SounddeviceBackend — captura y reproducción con sounddevice (PortAudio).

Plataformas: macOS (CoreAudio), Linux (ALSA/PulseAudio). No funciona en Termux
(Android) — para eso usar TermuxBackend.
"""
from __future__ import annotations

import asyncio
import logging
import queue as queue_mod
import threading

import numpy as np

from config import AudioConfig
from core.audio.preroll import PrerollBuffer
from core.audio.vad import is_silence as _is_silence
from .notification_tone import synth_notification_tone

log = logging.getLogger(__name__)

_TTS_SAMPLE_RATE = 24000  # PCM16 mono del gateway

# Tope de espera por cada stop()/close() de un stream de PortAudio (issue
# #87). Un driver colgado (Bluetooth desconectado a media reproducción) no
# debe bloquear el loop ni impedir el apagado: pasado este tiempo se loguea
# y se sigue. Constante de módulo, no clave de config.
_STREAM_CLOSE_TIMEOUT_S = 2.0


async def _call_off_loop(fn, timeout: float):
    """Ejecuta ``fn()`` en un hilo daemon propio y espera como mucho ``timeout``.

    No se usa el executor por defecto a propósito: un hilo colgado en el
    driver se quedaría en ese pool y ``asyncio.run`` lo espera al salir
    (``shutdown_default_executor``), así que el apagado se colgaría igual.
    Un hilo daemon no retiene la salida del proceso. Lanza TimeoutError si
    ``fn`` no vuelve a tiempo, o la excepción de ``fn``."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()

    def _resolve(exc: BaseException | None, result) -> None:
        if fut.done():
            return
        if exc is not None:
            fut.set_exception(exc)
        else:
            fut.set_result(result)

    def _runner() -> None:
        try:
            result, exc = fn(), None
        except BaseException as e:  # noqa: BLE001 - se reenvía al loop
            result, exc = None, e
        try:
            loop.call_soon_threadsafe(_resolve, exc, result)
        except RuntimeError:
            pass  # el loop ya cerró: nadie espera el resultado

    threading.Thread(target=_runner, daemon=True, name="sounddevice-close").start()
    return await asyncio.wait_for(fut, timeout)


async def _close_stream(stream, label: str) -> None:
    """stop() + close() de un stream fuera del loop, cada uno con su try:
    si stop() falla (dispositivo desaparecido) close() se intenta igual.

    Si stop() no vuelve a tiempo, su hilo sigue dentro de PortAudio y el
    stream se abandona sin close(): PortAudio no admite llamadas concurrentes
    sobre el mismo stream y cerrarlo desde otro hilo podría tumbar el
    proceso en vez de dejar un aviso en el log."""
    for step in ("stop", "close"):
        try:
            await _call_off_loop(getattr(stream, step), _STREAM_CLOSE_TIMEOUT_S)
        except (TimeoutError, asyncio.TimeoutError):
            log.warning(
                "SounddeviceBackend.%s %s: timeout (%.1fs), el driver no responde; "
                "se abandona el stream",
                label, step, _STREAM_CLOSE_TIMEOUT_S,
            )
            return
        except Exception as exc:
            log.warning("SounddeviceBackend.%s %s: %s", label, step, exc)


def _open_and_start(factory, **kwargs):
    """Crea un stream y lo arranca. Si .start() falla, cierra el stream
    antes de relanzar: si no, cada intento fallido deja el dispositivo
    ocupado por un stream de PortAudio huérfano."""
    stream = factory(**kwargs)
    try:
        stream.start()
    except BaseException:
        try:
            stream.close()
        except Exception as exc:
            log.warning("SounddeviceBackend: error cerrando stream que no arrancó: %s", exc)
        raise
    return stream


class SounddeviceBackend:
    """AudioBackend multiplataforma basado en sounddevice.

    Captura en `frames_per_buffer` chunks float32; VAD RMS; pre-roll ring-buffer.
    Reproduce chunks TTS int16 via OutputStream callback que lee de una queue.
    """

    def __init__(self, cfg: AudioConfig) -> None:
        self._cfg = cfg
        self._loop: asyncio.AbstractEventLoop | None = None
        self._capture_q: asyncio.Queue[bytes] | None = None
        self._oww_q: asyncio.Queue[bytes] | None = None
        self._input_stream = None
        self._output_stream = None
        self._play_q: queue_mod.Queue[bytes] = queue_mod.Queue()
        self._play_leftover: bytes | None = None
        self._enqueue_carry: bytes = b""
        self._capture_thread: threading.Thread | None = None
        self._capture_stop = threading.Event()

        self._preroll = PrerollBuffer(
            seconds=cfg.preroll_seconds,
            sample_rate=cfg.sample_rate,
            frames_per_buffer=cfg.frames_per_buffer,
        )
        self._lock = threading.Lock()
        # Lock propio del estado de reproducción (_play_q + _play_leftover),
        # compartido con el callback realtime de PortAudio (issue #88). No se
        # reutiliza self._lock: el hilo de captura lo toma en cada frame y el
        # callback de salida perdería bloques (silencio a mitad del TTS).
        # El callback solo lo intenta sin bloquear; el hilo de asyncio sí
        # espera (unos microsegundos: lo que tarda el callback en un bloque).
        # _enqueue_carry no lo necesita: solo se toca desde el hilo del loop.
        self._play_lock = threading.Lock()

    # --- captura ---

    async def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "SounddeviceBackend requiere sounddevice. pip install sounddevice"
            ) from exc

        self._loop = asyncio.get_running_loop()
        self._capture_q = asyncio.Queue()
        self._oww_q = asyncio.Queue()
        self._capture_stop.clear()

        def _callback(indata, frames, time, status):  # noqa: ARG001
            if status:
                log.warning("SounddeviceBackend capture status: %s", status)
            self._on_frame(bytes(indata))  # float32 contiguo

        kwargs = dict(
            samplerate=self._cfg.sample_rate,
            channels=self._cfg.channels,
            dtype="float32",
            blocksize=self._cfg.frames_per_buffer,
            callback=_callback,
        )
        if self._cfg.input_device is not None:
            kwargs["device"] = self._cfg.input_device

        self._input_stream = await self._open_input_stream_with_backoff(sd, kwargs)
        # _open_input_stream_with_backoff ya hace InputStream(...) + .start();
        # aquí solo queda registrar el stream en self y loguear el éxito.
        log.info("SounddeviceBackend: captura abierta (%d Hz, %d ch, block=%d)",
                 self._cfg.sample_rate, self._cfg.channels, self._cfg.frames_per_buffer)

    async def _open_input_stream_with_backoff(self, sd, kwargs: dict):
        """Abre ``sd.InputStream(**kwargs)`` y llama a ``.start()`` reintentando
        ante PortAudioError transitorio (issue #25). Patrón equivalente a
        ``OWWClient.connect_with_backoff()``: la lista de ``start_retry_backoff_s``
        actúa como timing del sleep entre intentos Y como número máximo de
        intentos (su longitud). Si la lista está vacía, un solo intento.
        Tras agotar los reintentos se relanza la última excepción."""
        backoff = list(self._cfg.start_retry_backoff_s)
        if not backoff:
            return _open_and_start(sd.InputStream, **kwargs)

        last_exc: Exception | None = None
        for idx, delay in enumerate(backoff):
            try:
                return _open_and_start(sd.InputStream, **kwargs)
            except sd.PortAudioError as exc:
                last_exc = exc
                if idx == len(backoff) - 1:
                    break
                log.warning(
                    "SounddeviceBackend.start: PortAudioError transitorio (%s), "
                    "reintentando en %.2fs (intento %d/%d)",
                    exc, delay, idx + 1, len(backoff),
                )
                await asyncio.sleep(delay)
        assert last_exc is not None  # siempre hay excepción si llegamos aquí
        raise last_exc

    async def stop(self) -> None:
        self._capture_stop.set()
        # Se sueltan las referencias ANTES de esperar: así un drain()/stop()
        # concurrente no vuelve a cerrar el mismo stream ni lee un None a
        # mitad de camino. El cierre va fuera del loop (issue #87).
        input_stream, self._input_stream = self._input_stream, None
        output_stream, self._output_stream = self._output_stream, None
        if input_stream is not None:
            await _close_stream(input_stream, "stop input")
        if output_stream is not None:
            await _close_stream(output_stream, "stop output")
        self._clear_playback()

    def _clear_playback(self) -> None:
        """Vacía la cola de reproducción y el resto pendiente del callback.

        Toma _play_lock para no intercalarse con un bloque del callback
        realtime: sin él, el callback podía sacar un chunk, reset() vaciar
        todo y después el callback guardar el resto del chunk viejo en
        _play_leftover, que sonaba al principio del turno siguiente (#88)."""
        self._enqueue_carry = b""
        with self._play_lock:
            self._play_leftover = None
            while True:
                try:
                    self._play_q.get_nowait()
                except queue_mod.Empty:
                    break

    # --- acceso a datos ---

    def _on_frame(self, chunk: bytes) -> None:
        """Punto único de entrada de cada frame capturado — hace fan-out a
        las dos colas independientes (RECORDING y OWW, ver get_queue() /
        get_oww_queue()) para que ningún consumidor le robe frames al otro
        (issue #9: antes ambos hacían `await q.get()` sobre la misma cola)."""
        with self._lock:
            self._preroll.append(chunk)
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._capture_q.put_nowait, chunk)
        self._loop.call_soon_threadsafe(self._oww_q.put_nowait, chunk)

    def get_queue(self) -> asyncio.Queue[bytes]:
        if self._capture_q is None:
            raise RuntimeError("SounddeviceBackend.get_queue() antes de start()")
        return self._capture_q

    def get_oww_queue(self) -> asyncio.Queue[bytes]:
        if self._oww_q is None:
            raise RuntimeError("SounddeviceBackend.get_oww_queue() antes de start()")
        return self._oww_q

    def get_preroll(self) -> bytes:
        with self._lock:
            return self._preroll.get()

    def is_silence(self, frame: bytes) -> bool:
        return _is_silence(frame, threshold_rms=self._cfg.vad_rms_threshold / 32768.0)

    # --- reproducción ---

    def _ensure_output_stream(self) -> None:
        if self._output_stream is not None:
            return
        import sounddevice as sd

        out_kwargs = dict(
            samplerate=_TTS_SAMPLE_RATE,
            channels=1,
            dtype="int16",
        )
        if self._cfg.output_device is not None:
            out_kwargs["device"] = self._cfg.output_device

        # Solo se guarda si arrancó: antes se asignaba antes de .start() y, si
        # start fallaba, las llamadas siguientes veían "ya hay stream" y la
        # reproducción quedaba en silencio para siempre.
        self._output_stream = _open_and_start(
            sd.OutputStream, **out_kwargs, callback=self._out_callback
        )

    def _out_callback(self, outdata, frames, time, status) -> None:  # noqa: ARG002
        # IMPORTANTE: este callback corre en el hilo realtime de PortAudio,
        # registrado por sounddevice con error=paAbort — cualquier excepción
        # sin capturar que escape de aquí aborta el OutputStream ENTERO
        # (no solo el chunk en curso), dejando el resto de la respuesta en
        # silencio permanente sin ningún error visible para el usuario. Por
        # eso nunca debe poder lanzar, pase lo que pase en la cola.
        # Nunca esperar el lock en el hilo realtime: si el hilo de asyncio
        # está vaciando la cola (reset/drain/stop), este bloque sale en
        # silencio y el siguiente ya ve el estado limpio (issue #88).
        if not self._play_lock.acquire(blocking=False):
            outdata[:, 0] = 0
            return
        try:
            self._out_callback_unsafe(outdata, frames, status)
        except Exception:
            log.exception("SounddeviceBackend: error en _out_callback, escribiendo silencio")
            outdata[:, 0] = 0
        finally:
            self._play_lock.release()

    def _out_callback_unsafe(self, outdata, frames, status) -> None:
        if status:
            log.warning("SounddeviceBackend playback status: %s", status)
        written = 0
        while written < frames:
            if self._play_leftover is not None:
                chunk = self._play_leftover
                self._play_leftover = None
            else:
                try:
                    chunk = self._play_q.get_nowait()
                except queue_mod.Empty:
                    break
            arr = np.frombuffer(chunk, dtype=np.int16)
            n = min(len(arr), frames - written)
            outdata[written:written + n, 0] = arr[:n]
            written += n
            if n < len(arr):
                self._play_leftover = arr[n:].tobytes()
        if written < frames:
            outdata[written:, 0] = 0

    async def play_notification(self) -> None:
        wave = synth_notification_tone(_TTS_SAMPLE_RATE)
        await self._enqueue_and_wait(wave)

    async def play_chunk(self, audio: bytes) -> None:
        if not audio:
            return
        await self._enqueue_and_wait(audio)

    async def _enqueue_and_wait(self, audio: bytes) -> None:
        # El chunking de red no garantiza alinear cada mensaje TTS a un
        # número par de bytes (tamaño de una muestra int16) — un chunk de
        # longitud impar rompería np.frombuffer en _out_callback. En vez de
        # dejarlo entrar en _play_q, arrastramos el byte suelto y lo
        # anteponemos al siguiente chunk para no perder muestras.
        audio = self._enqueue_carry + audio
        if len(audio) % 2 != 0:
            self._enqueue_carry = audio[-1:]
            audio = audio[:-1]
        else:
            self._enqueue_carry = b""
        if not audio:
            return
        duration = len(audio) / (_TTS_SAMPLE_RATE * 2)
        # El audio debe estar encolado ANTES de arrancar el stream: si
        # _ensure_output_stream() (que llama a stream.start()) corre primero,
        # el callback de PortAudio puede pedir datos con _play_q aún vacía
        # justo al arrancar, reproduciendo silencio y perdiendo el principio
        # del audio (click al inicio de cada beep/respuesta).
        self._play_q.put(audio)
        self._ensure_output_stream()
        # Dormir solo la duración real del chunk: _play_q ya desacopla
        # productor/consumidor, así que no hace falta (ni conviene) un
        # margen extra aquí — dormir de más retrasa la entrega del
        # siguiente chunk más de lo que el hardware tarda en consumir el
        # actual, vaciando la cola y produciendo huecos de silencio real
        # entre chunk y chunk (no distorsión: samples que no se juntan).
        await asyncio.sleep(duration)

    async def drain(self) -> None:
        output_stream, self._output_stream = self._output_stream, None
        if output_stream is not None:
            await _close_stream(output_stream, "drain")
        self._clear_playback()

    def reset(self) -> None:
        with self._lock:
            self._preroll.clear()
        self._clear_playback()
