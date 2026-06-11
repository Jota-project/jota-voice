"""Wyoming OpenWakeWord client.

Connects to wyoming-openwakeword (default port 10401) using the Wyoming
JSON-lines-over-TCP protocol and reports wake-word detections.
"""

import asyncio
import json
import logging
from typing import Optional

from config import OWWConfig

log = logging.getLogger(__name__)


class OWWClient:
    def __init__(self, cfg: OWWConfig) -> None:
        self._cfg = cfg
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        """Open TCP connection and send audio-start handshake."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._cfg.host, self._cfg.port),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            raise OSError(f"OWW: timeout conectando a {self._cfg.host}:{self._cfg.port}")
        try:
            await self._send_json(
                {
                    "type": "audio-start",
                    "data": {"rate": 16000, "width": 2, "channels": 1},
                    "data_length": 0,
                }
            )
        except Exception:
            await self.disconnect()
            raise
        self._connected = True
        log.debug("OWW conectado a %s:%d", self._cfg.host, self._cfg.port)

    async def disconnect(self) -> None:
        """Close the TCP connection gracefully."""
        self._connected = False
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def send_audio(self, pcm_int16: bytes) -> None:
        """Send one chunk of raw PCM-16 audio to the wakeword service."""
        if not self._connected or self._writer is None:
            raise ConnectionError("OWWClient: no conectado")
        header = {
            "type": "audio-chunk",
            "data": {"rate": 16000, "width": 2, "channels": 1, "timestamp": 0},
            "payload_length": len(pcm_int16),
        }
        await self._send_json(header)
        self._writer.write(pcm_int16)
        await self._writer.drain()

    async def wait_for_detection(self) -> str:
        """Block until a configured wake word is detected.

        Skips any detection whose name is not in ``cfg.wake_words``.
        Raises ``ConnectionError`` if the server closes the connection.
        """
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    raise ConnectionError("OWW cerró la conexión")
                try:
                    msg = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue
                if msg.get("data_length", 0) > 0:
                    data_bytes = await self._reader.readexactly(msg["data_length"])
                    try:
                        msg.setdefault("data", {}).update(json.loads(data_bytes))
                    except json.JSONDecodeError:
                        pass
                if msg.get("payload_length", 0) > 0:
                    await self._reader.readexactly(msg["payload_length"])
                if msg.get("type") == "detection":
                    name = msg.get("data", {}).get("name", "")
                    # OWW envía "ok_nabu_v0.1"; config tiene "ok_nabu" — tolerar versión
                    matched = next(
                        (ww for ww in self._cfg.wake_words
                         if name == ww or name.startswith(ww + "_")),
                        None,
                    )
                    if matched:
                        log.info("Wake word detectado: %s", name)
                        return matched
                    log.debug("Detección ignorada (no configurada): %s", name)
        except (OSError, asyncio.IncompleteReadError, ConnectionError):
            self._connected = False
            raise

    async def connect_with_backoff(self) -> None:
        """Attempt ``connect()`` repeatedly, using exponential-ish back-off delays."""
        backoff = list(self._cfg.reconnect_backoff_s)
        if not backoff:
            raise ValueError("OWWConfig.reconnect_backoff_s no puede estar vacío")
        idx = 0
        while True:
            try:
                await self.connect()
                return
            except OSError as exc:
                delay = backoff[min(idx, len(backoff) - 1)]
                log.warning(
                    "OWW no disponible (%s), reintentando en %.0fs", exc, delay
                )
                await asyncio.sleep(delay)
                idx += 1

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send_json(self, obj: dict) -> None:
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        self._writer.write(line.encode())
        await self._writer.drain()
