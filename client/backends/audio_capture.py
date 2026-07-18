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
import collections
import logging
import os
import subprocess
import threading
from typing import Optional

import numpy as np

from config import AudioConfig

logger = logging.getLogger(__name__)


def _int16_to_float32(data: bytes) -> bytes:
    """Convierte frames PCM int16 a float32 normalizado [-1.0, 1.0]."""
    arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    return arr.tobytes()


class AudioCapture:
    """Captura audio del micrófono y lo entrega como float32 por asyncio.Queue."""

    def __init__(self, cfg: AudioConfig) -> None:
        self._cfg = cfg
        self._proc: Optional[subprocess.Popen] = None
        self._queue: Optional[asyncio.Queue[bytes]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._read_thread: Optional[threading.Thread] = None

        preroll_frames = int(
            cfg.preroll_seconds * cfg.sample_rate / cfg.frames_per_buffer
        )
        self._preroll: collections.deque[bytes] = collections.deque(
            maxlen=preroll_frames
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Arranca parec y el hilo lector. Reintenta con backoff si parec muere."""
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
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
        while not self._stop_event.is_set():
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env=env,
                )
                # Verificar que arrancó de verdad (no murió instantáneo)
                await asyncio.sleep(0.5)
                if self._proc.poll() is not None:
                    logger.warning(
                        "AudioCapture: parec murió al arrancar (rc=%s), reintentando en %.1fs",
                        self._proc.returncode, backoff,
                    )
                    self._proc = None
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 10.0)
                    continue
                backoff = 1.0
                self._read_thread = threading.Thread(
                    target=self._read_loop, daemon=True, name="audio-capture"
                )
                self._read_thread.start()
                return
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

    async def stop(self) -> None:
        """Detiene el proceso parec y el hilo lector."""
        logger.debug("AudioCapture detenido")
        self._stop_event.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2.0)
            except Exception:
                self._proc.kill()
            self._proc = None
        if self._read_thread is not None:
            self._read_thread.join(timeout=2.0)
            self._read_thread = None

    # ------------------------------------------------------------------
    # Acceso a datos
    # ------------------------------------------------------------------

    def get_queue(self) -> asyncio.Queue[bytes]:
        """Devuelve la queue donde se encolan los frames float32."""
        if self._queue is None:
            raise RuntimeError("AudioCapture.get_queue() llamado antes de start()")
        return self._queue

    def get_preroll(self) -> bytes:
        """Devuelve los últimos N segundos de audio como bytes float32 concatenados."""
        with self._lock:
            return b"".join(self._preroll)

    def is_silence(self, frame: bytes) -> bool:
        """
        Devuelve True si el frame está por debajo del umbral de VAD.

        El frame se interpreta como float32 normalizado; se escala a rango int16
        para comparar con vad_rms_threshold (umbral expresado en unidades int16).
        """
        samples = np.frombuffer(frame, dtype=np.float32)
        rms = float(np.sqrt(np.mean(samples ** 2))) * 32768.0
        return rms < self._cfg.vad_rms_threshold

    # ------------------------------------------------------------------
    # Hilo lector
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        """Lee frames de parec y los encola en asyncio."""
        bytes_per_frame = self._cfg.frames_per_buffer * 2  # int16 = 2 bytes/sample
        _first = True
        while not self._stop_event.is_set():
            try:
                in_data = self._proc.stdout.read(bytes_per_frame)
            except Exception as exc:
                logger.warning("AudioCapture._read_loop: error leyendo parec: %s", exc)
                break
            if not in_data:
                logger.warning("AudioCapture._read_loop: parec cerró stdout")
                break
            float32_bytes = _int16_to_float32(in_data)
            if _first:
                arr = np.frombuffer(float32_bytes, np.float32)
                rms = float(np.sqrt(np.mean(arr ** 2))) * 32768.0
                logger.info("AudioCapture: primer frame de parec, RMS=%.1f", rms)
                _first = False
            with self._lock:
                self._preroll.append(float32_bytes)
            if self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._queue.put_nowait, float32_bytes)
