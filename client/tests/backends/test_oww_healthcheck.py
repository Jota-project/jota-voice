"""TDD del fix de issue #22: run_forever publica al EventBus cuando OWW
está degradado (N reintentos consecutivos fallidos) y cuando se recupera.

Estrategia de los tests: monkeypatch de oww_client.OWWClient.connect,
connect_with_backoff, wait_for_detection y disconnect con side_effects
programables. EventBus real (no mock) — nos suscribimos a su async
generator para que las queues internas existan y los eventos se entreguen
(sin suscriptores, publish() es no-op porque EventBus._queues está vacío).
run_forever se envuelve en asyncio.wait_for para forzar terminación.

Simplificación documentada: en lugar de hacer mocks independientes por
cada test, helper _Collector suscribe al bus y captura todos los eventos
en una lista. Filtramos por tipo al final. Es coverage parcial porque
depende de que el bucle del test llegue al punto donde se publica el
evento — pero como patcheamos wait_for_detection/connect_with_backoff
para controlar el flujo, es fiable.
"""
from __future__ import annotations

import asyncio
from typing import Any, List
from unittest.mock import AsyncMock, patch

import pytest

from config import AudioConfig, Config, DeviceConfig, GatewayConfig, OWWConfig
from domain.event_bus import EventBus, VoiceEvent
from backends.oww_client import OWWClient


def _cfg(**oww_overrides: Any) -> Config:
    """Helper: Config mínima con un OWWConfig al que se le pueden sobreescribir campos.

    Usamos setattr en lugar de OWWConfig(**oww_overrides) para que en RED
    (cuando oww_healthcheck_threshold aún no existe en el dataclass) el
    helper no falle con TypeError — el atributo simplemente se crea como
    atributo de instancia, que es lo que el código bajo test va a leer
    vía self._cfg.oww_healthcheck_threshold.
    """
    oww = OWWConfig()
    for k, v in oww_overrides.items():
        setattr(oww, k, v)
    return Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="x"),
        device=DeviceConfig(id="test"),
        oww=oww,
        audio=AudioConfig(),
    )


class _Collector:
    """Context manager async: suscribe al bus y captura eventos en una lista.

    Como EventBus.publish() solo entrega a las queues que existan en
    self._queues, necesitamos un suscriptor vivo ANTES de que run_forever
    publique. Esta clase arranca un task que hace async for sobre
    bus.subscribe() — al iterar por primera vez, la queue se registra.
    """

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.events: List[VoiceEvent] = []
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "_Collector":
        async def _run() -> None:
            async for ev in self.bus.subscribe():
                self.events.append(ev)
        self._task = asyncio.create_task(_run())
        # Ceder control una vez para que la task arranque y self._queues
        # se rellene con la queue del suscriptor.
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def of_type(self, event_type: str) -> List[VoiceEvent]:
        return [e for e in self.events if e.type == event_type]


@pytest.mark.asyncio
async def test_run_forever_publishes_oww_degraded_after_threshold_failures() -> None:
    """Tras oww_healthcheck_threshold (default 3) reintentos consecutivos
    fallidos, run_forever publica UN evento oww_degraded."""
    cfg = _cfg(reconnect_backoff_s=[0.001])  # backoffs mínimos para que el test quepa en 0.3s
    bus = EventBus()
    client = OWWClient(cfg.oww, audio_cfg=cfg.audio, bus=bus)

    async with _Collector(bus) as col:
        with patch.object(OWWClient, "connect", new=AsyncMock(return_value=None)), \
             patch.object(OWWClient, "connect_with_backoff", new=AsyncMock(return_value=None)), \
             patch.object(OWWClient, "wait_for_detection",
                          new=AsyncMock(side_effect=ConnectionError("disconnect"))), \
             patch.object(OWWClient, "disconnect", new=AsyncMock(return_value=None)):
            try:
                await asyncio.wait_for(
                    client.run_forever(audio=None, on_wake_word=lambda n: asyncio.sleep(0)),
                    timeout=1.5,  # holgura: el primer run_forever importa numpy
                )
            except asyncio.TimeoutError:
                pass  # esperado: el loop solo termina por timeout externo

        # Dejar que el collector drene cualquier evento en vuelo antes de cancelar.
        await asyncio.sleep(0)

    degraded = col.of_type("oww_degraded")
    assert len(degraded) == 1, (
        f"esperaba exactamente 1 evento oww_degraded tras 3 fallos, "
        f"obtuve {len(degraded)}"
    )
    assert degraded[0].data["consecutive_failures"] == 3


@pytest.mark.asyncio
async def test_run_forever_publishes_oww_recovered_after_successful_reconnect() -> None:
    """Tras 2 fallos, una reconexión exitosa, publicar oww_recovered (1 vez)."""
    cfg = _cfg(oww_healthcheck_threshold=2, reconnect_backoff_s=[0.001])
    bus = EventBus()
    client = OWWClient(cfg.oww, audio_cfg=cfg.audio, bus=bus)

    call_idx = {"n": 0}

    async def fake_wfd() -> str:
        call_idx["n"] += 1
        n = call_idx["n"]
        if n <= 2:
            raise ConnectionError(f"fail #{n}")
        if n == 3:
            return "ok_nabu"  # éxito → activa oww_recovered
        # Tras el éxito, bloqueamos para que el test termine por timeout
        # y no se acumulen más fallos que disparen otro oww_degraded.
        await asyncio.Event().wait()
        return "unreachable"  # pragma: no cover

    async with _Collector(bus) as col:
        with patch.object(OWWClient, "connect", new=AsyncMock(return_value=None)), \
             patch.object(OWWClient, "connect_with_backoff", new=AsyncMock(return_value=None)), \
             patch.object(OWWClient, "wait_for_detection", side_effect=fake_wfd), \
             patch.object(OWWClient, "disconnect", new=AsyncMock(return_value=None)):
            try:
                await asyncio.wait_for(
                    client.run_forever(audio=None, on_wake_word=lambda n: asyncio.sleep(0)),
                    timeout=1.5,  # holgura: el primer run_forever importa numpy
                )
            except asyncio.TimeoutError:
                pass

        await asyncio.sleep(0)

    degraded = col.of_type("oww_degraded")
    recovered = col.of_type("oww_recovered")
    assert len(degraded) == 1, f"esperaba 1 oww_degraded, obtuve {len(degraded)}"
    assert len(recovered) == 1, f"esperaba 1 oww_recovered, obtuve {len(recovered)}"


@pytest.mark.asyncio
async def test_run_forever_no_publish_when_bus_is_none() -> None:
    """Sin bus inyectado, run_forever no publica nada y no lanza — comportamiento
    legacy preservado."""
    cfg = _cfg(reconnect_backoff_s=[0.001])
    client = OWWClient(cfg.oww, audio_cfg=cfg.audio, bus=None)

    # connect_with_backoff tiene que "tener éxito" para que el código entre
    # al bucle interno; las excepciones lanzadas por él escapan del try
    # externo de run_forever. La no-publicación se prueba forzando drops en
    # wait_for_detection, que sí está cubierto por el catch.
    with patch.object(OWWClient, "connect", new=AsyncMock(return_value=None)), \
         patch.object(OWWClient, "connect_with_backoff", new=AsyncMock(return_value=None)), \
         patch.object(OWWClient, "wait_for_detection",
                      new=AsyncMock(side_effect=ConnectionError("nope"))), \
         patch.object(OWWClient, "disconnect", new=AsyncMock(return_value=None)):
        try:
            await asyncio.wait_for(
                client.run_forever(audio=None, on_wake_word=lambda n: asyncio.sleep(0)),
                timeout=0.2,
            )
        except asyncio.TimeoutError:
            pass  # esperado


def test_oww_config_has_default_healthcheck_threshold() -> None:
    """OWWConfig.oww_healthcheck_threshold debe tener default 3."""
    cfg = OWWConfig()
    assert hasattr(cfg, "oww_healthcheck_threshold"), (
        "OWWConfig no tiene oww_healthcheck_threshold — el fix no se aplicó"
    )
    assert cfg.oww_healthcheck_threshold == 3, (
        f"default esperaba 3, obtuve {cfg.oww_healthcheck_threshold}"
    )