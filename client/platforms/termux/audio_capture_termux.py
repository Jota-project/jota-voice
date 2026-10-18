"""
AudioCapture — captura de micrófono via parec (PulseAudio) con pre-roll y VAD por RMS.

Responsabilidades:
  - Proceso parec → hilo lector → asyncio.Queue (float32 bytes)
  - Ring-buffer de pre-roll (últimos N segundos)
  - is_silence() por RMS

PyAudio/PortAudio en Termux/Android usa ALSA directamente y devuelve silencio.
parec usa PulseAudio → OpenSL_ES_source → micrófono real.
"""

import asyncio
import functools
import logging
import os
import subprocess
import threading
from typing import Optional

import numpy as np

from config import AudioConfig
from core.audio.framing import int16_to_float32
from core.audio.offloop import call_off_loop
from core.audio.preroll import PrerollBuffer
from core.audio.vad import is_silence as _is_silence

logger = logging.getLogger(__name__)

# Tope de la parada completa fuera del loop: terminate + wait(2) [+ kill +
# wait(2)] + join(2) suman ~6 s en el peor caso; esto es solo la red de
# seguridad por si algo se cuelga fuera de esos timeouts.
_STOP_TIMEOUT_S = 8.0


class AudioCapture:
    """Captura audio del micrófono y lo entrega como float32 por asyncio.Queue."""

    def __init__(self, cfg: AudioConfig) -> None:
        self._cfg = cfg
        self._proc: Optional[subprocess.Popen] = None
        self._queue: Optional[asyncio.Queue[bytes]] = None
        self._oww_queue: Optional[asyncio.Queue[bytes]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._read_thread: Optional[threading.Thread] = None
        # Generación de arranque: start() y stop() la incrementan. Un start()
        # que despierta con otra generación sabe que lo pararon (y quizá
        # llegó otro start() después) y se retira sin tocar el estado nuevo.
        self._gen = 0

        self._preroll = PrerollBuffer(
            seconds=cfg.preroll_seconds,
            sample_rate=cfg.sample_rate,
            frames_per_buffer=cfg.frames_per_buffer,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> bool:
        """Arranca parec y el hilo lector. Reintenta con backoff si parec muere.

        Devuelve True si la captura quedó en marcha y False si la pararon
        durante el arranque (stop() mientras se esperaba a parec)."""
        self._gen += 1
        gen = self._gen
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._oww_queue = asyncio.Queue()
        self._stop_event.clear()

        pulse_path = os.environ.get("PULSE_RUNTIME_PATH", os.path.expanduser("~/.pulse"))
        env = {**os.environ, "PULSE_RUNTIME_PATH": pulse_path}

        cmd = [
            "parec",
            "--device=OpenSL_ES_source",
            f"--rate={self._cfg.sample_rate}",
            "--channels=1",
            "--format=s16le",
            "--latency-msec=50",
        ]
        logger.debug("AudioCapture: arrancando %s", " ".join(cmd))

        # Bucle de reintento: si parec muere (PulseAudio aún no listo, HAL no
        # inicializado, etc.) re-lanzarlo hasta que funcione o nos paren.
        backoff = 1.0
        while not self._stop_event.is_set() and gen == self._gen:
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env=env,
                )
                self._proc = proc
                # Verificar que arrancó de verdad (no murió instantáneo)
                await asyncio.sleep(0.5)
                if self._stop_event.is_set() or gen != self._gen:
                    # stop() llegó durante la espera (ahora cede el loop), y
                    # quizá otro start() después. Si stop() ya se llevó el
                    # proceso, él lo mata; si no, lo hacemos aquí. Nunca se
                    # toca el proceso de un start() posterior ni se arranca
                    # el hilo lector.
                    if self._proc is proc:
                        self._proc = None
                        await self._off_loop(functools.partial(self._terminate_proc, proc))
                    return False
                if proc.poll() is not None:
                    logger.warning(
                        "AudioCapture: parec murió al arrancar (rc=%s), reintentando en %.1fs",
                        proc.returncode, backoff,
                    )
                    if self._proc is proc:
                        self._proc = None
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 10.0)
                    continue
                backoff = 1.0
                # El proceso va como argumento: el hilo no relee self._proc,
                # que stop() pone a None mientras el hilo sigue leyendo.
                self._read_thread = threading.Thread(
                    target=self._read_loop, args=(proc,), daemon=True, name="audio-capture"
                )
                self._read_thread.start()
                return True
            except FileNotFoundError:
                logger.error("AudioCapture: parec no encontrado en PATH")
                raise
            except Exception as exc:
                logger.warning(
                    "AudioCapture: error arrancando parec: %s, reintentando en %.1fs",
                    exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
        return False

    async def stop(self) -> None:
        """Detiene el proceso parec y el hilo lector.

        Las esperas (hasta 2 s por wait() de parec y 2 s por el join del
        hilo, ~6 s en el peor caso) corren en un hilo daemon: en el hilo del
        loop congelaban OWW, gateway y display durante el apagado. El aviso
        de parada y la suelta de referencias son síncronos, así un start()
        en curso ve el stop al despertar y no arranca el hilo lector."""
        logger.debug("AudioCapture detenido")
        self._stop_event.set()
        self._gen += 1
        proc, self._proc = self._proc, None
        thread, self._read_thread = self._read_thread, None
        if proc is None and thread is None:
            return
        await self._off_loop(functools.partial(self._stop_blocking, proc, thread))

    @staticmethod
    async def _off_loop(fn) -> None:
        """Hilo daemon con tope (core.audio.offloop), no el executor por
        defecto: sus hilos no son daemon y retendrían la salida del proceso."""
        try:
            await call_off_loop(fn, _STOP_TIMEOUT_S, name="audio-capture-stop")
        except (TimeoutError, asyncio.TimeoutError):
            logger.error("AudioCapture: la parada de parec no terminó en %.1fs", _STOP_TIMEOUT_S)

    @classmethod
    def _stop_blocking(
        cls, proc: Optional[subprocess.Popen], thread: Optional[threading.Thread]
    ) -> None:
        if proc is not None:
            cls._terminate_proc(proc)
        if thread is not None:
            thread.join(timeout=2.0)

    @staticmethod
    def _terminate_proc(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except Exception:
            # Tras kill() hay que volver a hacer wait() para recoger
            # (reap) el proceso; si no, parec queda zombie (issue #86).
            try:
                proc.kill()
                proc.wait(timeout=2.0)
            except Exception as exc:
                logger.warning("AudioCapture: no se pudo recoger parec tras kill(): %s", exc)

    # ------------------------------------------------------------------
    # Acceso a datos
    # ------------------------------------------------------------------

    def get_queue(self) -> asyncio.Queue[bytes]:
        """Devuelve la queue donde se encolan los frames float32."""
        if self._queue is None:
            raise RuntimeError("AudioCapture.get_queue() llamado antes de start()")
        return self._queue

    def get_oww_queue(self) -> asyncio.Queue[bytes]:
        """Cola independiente de la de get_queue(), alimentada por el mismo
        punto de captura — evita que OWW y el consumidor de RECORDING se
        roben frames mutuamente (issue #9)."""
        if self._oww_queue is None:
            raise RuntimeError("AudioCapture.get_oww_queue() llamado antes de start()")
        return self._oww_queue

    def get_preroll(self) -> bytes:
        """Devuelve los últimos N segundos de audio como bytes float32 concatenados."""
        with self._lock:
            return self._preroll.get()

    def is_silence(self, frame: bytes) -> bool:
        """Devuelve True si el frame está por debajo del umbral de VAD.

        El frame se interpreta como float32 normalizado (vad_rms_threshold está
        expresado en unidades int16 en la config; se normaliza aquí).
        """
        return _is_silence(frame, threshold_rms=self._cfg.vad_rms_threshold / 32768.0)

    def reset(self) -> None:
        """Limpia el preroll entre turnos (PlaybackEngine.reset() / StateMachine).

        Sin esto, audio capturado antes de un turno cancelado se cuela en el
        siguiente wake-word. Asimetría histórica con SounddeviceBackend que se
        resuelve en este fix de revisión post-Fase A."""
        with self._lock:
            self._preroll.clear()

    # ------------------------------------------------------------------
    # Hilo lector
    # ------------------------------------------------------------------

    def _read_loop(self, proc: subprocess.Popen) -> None:
        """Lee frames de parec y los encola en asyncio."""
        bytes_per_frame = self._cfg.frames_per_buffer * 2  # int16 = 2 bytes/sample
        stdout = proc.stdout
        if stdout is None:  # Popen siempre se crea con stdout=PIPE
            logger.warning("AudioCapture._read_loop: parec sin stdout")
            return
        _first = True
        while not self._stop_event.is_set():
            try:
                in_data = stdout.read(bytes_per_frame)
            except Exception as exc:
                logger.warning("AudioCapture._read_loop: error leyendo parec: %s", exc)
                break
            if not in_data:
                logger.warning("AudioCapture._read_loop: parec cerró stdout")
                break
            float32_bytes = int16_to_float32(in_data)
            if _first:
                arr = np.frombuffer(float32_bytes, np.float32)
                rms = float(np.sqrt(np.mean(arr ** 2))) * 32768.0
                logger.info("AudioCapture: primer frame de parec, RMS=%.1f", rms)
                _first = False
            self._on_frame(float32_bytes)

    def _on_frame(self, float32_bytes: bytes) -> None:
        """Punto único de entrada de cada frame capturado — hace fan-out a
        las dos colas independientes (RECORDING y OWW, ver get_queue() /
        get_oww_queue()) para que ningún consumidor le robe frames al otro
        (issue #9: antes ambos hacían `await q.get()` sobre la misma cola)."""
        with self._lock:
            self._preroll.append(float32_bytes)
        loop, queue, oww_queue = self._loop, self._queue, self._oww_queue
        if loop is None or loop.is_closed() or queue is None or oww_queue is None:
            return
        loop.call_soon_threadsafe(queue.put_nowait, float32_bytes)
        loop.call_soon_threadsafe(oww_queue.put_nowait, float32_bytes)
