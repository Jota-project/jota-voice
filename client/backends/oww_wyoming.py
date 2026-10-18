"""WyomingBackend — wake word via Wyoming TCP (default port 10401)."""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from config import AudioConfig, OWWConfig
from .oww_client import OWWClient


class WyomingBackend:
    """Backend de wake word usando el protocolo Wyoming (JSON-lines over TCP).

    Funciona con cualquier wyoming-openwakeword (Docker en Mac, venv en Termux).

    El callback de wake word se recibe solo en `run_forever(audio, on_wake_word)`
    (obligatorio, igual que en el Protocol `OwWBackend`). Antes también se
    aceptaba en el constructor como opcional y se validaba en runtime, pero el
    único llamador real (voice_client.oww_loop) siempre lo pasa a run_forever:
    era una rama muerta (issue #90).

    `audio_cfg` (issue #14) es la AudioConfig del backend de audio para que el
    OWWClient subyacente declare el rate/channels REALES del micrófono en los
    eventos Wyoming, en lugar de asumir 16000/mono. Es opcional para mantener
    compatibilidad con código que construye WyomingBackend solo con OWWConfig.
    """

    def __init__(
        self,
        cfg: OWWConfig,
        audio_cfg: Optional[AudioConfig] = None,
        bus=None,
    ) -> None:
        self._cfg = cfg
        # bus: para que OWWClient publique oww_degraded/oww_recovered.
        self._client = OWWClient(cfg, audio_cfg=audio_cfg, bus=bus)

    async def connect_with_backoff(self) -> None:
        await self._client.connect_with_backoff()

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def send_audio(self, pcm_int16: bytes) -> None:
        await self._client.send_audio(pcm_int16)

    async def wait_for_detection(self) -> str:
        return await self._client.wait_for_detection()

    async def run_forever(self, audio, on_wake_word: Callable[[str], Awaitable[None]]) -> None:
        await self._client.run_forever(audio, on_wake_word)
