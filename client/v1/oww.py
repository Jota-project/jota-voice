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

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(
            self._cfg.host, self._cfg.port
        )
        self._connected = True
        await self._send_json(
            {"type": "audio-start", "data": {"rate": 16000, "width": 2, "channels": 1}, "data_length": 0}
        )
        log.debug("OWW conectado a %s:%d", self._cfg.host, self._cfg.port)

    async def disconnect(self) -> None:
        self._connected = False
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def send_audio_chunk(self, pcm_int16: bytes) -> None:
        header = {
            "type": "audio-chunk",
            "data": {"rate": 16000, "width": 2, "channels": 1},
            "data_length": len(pcm_int16),
        }
        await self._send_json(header)
        self._writer.write(pcm_int16)
        await self._writer.drain()

    async def wait_for_detection(self) -> str:
        while True:
            line = await self._reader.readline()
            if not line:
                raise ConnectionError("OWW cerró la conexión")
            try:
                msg = json.loads(line.decode().strip())
            except json.JSONDecodeError:
                continue
            if msg.get("data_length", 0) > 0:
                await self._reader.readexactly(msg["data_length"])
            if msg.get("type") == "detection":
                name = msg.get("data", {}).get("name", "")
                log.info("Wake word detectado: %s", name)
                return name

    async def connect_with_backoff(self) -> None:
        backoff = list(self._cfg.reconnect_backoff_s)
        idx = 0
        while True:
            try:
                await self.connect()
                return
            except OSError as exc:
                delay = backoff[min(idx, len(backoff) - 1)]
                log.warning("OWW no disponible (%s), reintentando en %ds", exc, delay)
                await asyncio.sleep(delay)
                idx += 1

    async def _send_json(self, obj: dict) -> None:
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        self._writer.write(line.encode())
        await self._writer.drain()
