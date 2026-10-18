"""TDD del fix de issue #22: run_forever publica al EventBus cuando OWW
está degradado (N reintentos consecutivos fallidos) y cuando se recupera.

Estrategia de los tests: monkeypatch de oww_client.OWWClient.connect,
connect_with_backoff, wait_for_detection y disconnect con side_effects
programables. EventBus real (no mock) — nos suscribimos a su async
generator para que las queues internas existan y los eventos se entreguen
(sin suscriptores, publish() es no-op porque EventBus._queues está vacío).

Terminación (issue #76): run_forever corre como task y el test la cancela
cuando wait_for_detection se ha llamado un número concreto de veces, no
tras un ``asyncio.wait_for`` con timeout de pared. Con un timeout fijo el
número de vueltas del bucle dependía de la carga de la máquina, y además
destapaba un bug real (run_forever se tragaba la cancelación si llegaba
durante la limpieza de send_task — ver test_oww_client.py).
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, List
from unittest.mock import patch

import pytest

from config import AudioConfig, Config, DeviceConfig, GatewayConfig, OWWConfig
from domain.event_bus import EventBus, VoiceEvent
from backends.oww_client import OWWClient

# Red de seguridad: ningún test debería acercarse a esto; si lo alcanza es
# que el bucle no avanza (o no termina al cancelarlo).
_SAFETY_TIMEOUT_S = 5.0


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


class _EmptyAudio:
    """AudioBackend fake: la cola nunca produce frames."""

    def get_oww_queue(self) -> asyncio.Queue:
        return asyncio.Queue()


class _Collector:
    """Context manager async: suscribe al bus y captura eventos en una lista.

    Como EventBus.publish() solo entrega a las queues que existan en
    self._queues, necesitamos un suscriptor vivo ANTES de que run_forever
    publique. Esta clase arranca un task que hace async for sobre
    bus.subscribe() y espera a que la queue quede registrada.
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
        while not self.bus._queues:
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

    async def drain(self) -> None:
        """Deja que el collector consuma lo que ya está publicado."""
        for q in self.bus._queues:
            while not q.empty():
                await asyncio.sleep(0)

    def of_type(self, event_type: str) -> List[VoiceEvent]:
        return [e for e in self.events if e.type == event_type]


async def _noop_on_wake(name: str) -> None:
    pass


class _AbortLoop(Exception):
    """La lanza el wait_for_detection fake para sacar a run_forever de su
    bucle si no respondió a la cancelación (evita colgar el teardown)."""


class _CountingWFD:
    """wait_for_detection fake: delega en ``behaviour(n)`` y marca
    ``reached`` cuando se alcanza la llamada número ``stop_after``."""

    def __init__(self, stop_after: int, behaviour: Callable[[int], Awaitable[str]]) -> None:
        self.stop_after = stop_after
        self.behaviour = behaviour
        self.reached = asyncio.Event()
        self.abort = False
        self.calls = 0

    async def __call__(self, *_self) -> str:
        # *_self: con patch.object(..., autospec=True) llega la instancia.
        if self.abort:
            raise _AbortLoop()
        self.calls += 1
        if self.calls >= self.stop_after:
            self.reached.set()
        return await self.behaviour(self.calls)


async def _run_until(client: OWWClient, wfd: _CountingWFD) -> None:
    """Arranca run_forever, espera a ``wfd.reached`` y cancela. Falla si
    run_forever no termina cancelada (p.ej. si se traga la cancelación)."""
    task = asyncio.create_task(
        client.run_forever(audio=_EmptyAudio(), on_wake_word=_noop_on_wake)
    )
    try:
        await asyncio.wait_for(wfd.reached.wait(), timeout=_SAFETY_TIMEOUT_S)
    finally:
        task.cancel()
        done, _ = await asyncio.wait([task], timeout=_SAFETY_TIMEOUT_S)
        if task not in done:
            wfd.abort = True
            await asyncio.wait([task], timeout=_SAFETY_TIMEOUT_S)
    assert task in done, "run_forever no terminó tras cancelarla (se tragó la cancelación)"
    assert task.cancelled(), f"run_forever terminó sin cancelarse: {task!r}"


def _patches(wfd: _CountingWFD):
    """Parchea la E/S de OWWClient; wait_for_detection lo controla el test."""
    from contextlib import ExitStack

    stack = ExitStack()
    # autospec=True (issue #38): firma real, y un método inexistente falla.
    stack.enter_context(patch.object(OWWClient, "connect", autospec=True, return_value=None))
    stack.enter_context(
        patch.object(OWWClient, "connect_with_backoff", autospec=True, return_value=None)
    )
    stack.enter_context(
        patch.object(OWWClient, "wait_for_detection", autospec=True, side_effect=wfd.__call__)
    )
    stack.enter_context(patch.object(OWWClient, "disconnect", autospec=True, return_value=None))
    return stack


@pytest.mark.asyncio
async def test_run_forever_publishes_oww_degraded_after_threshold_failures() -> None:
    """Tras oww_healthcheck_threshold (default 3) reintentos consecutivos
    fallidos, run_forever publica UN evento oww_degraded (y no lo repite en
    los fallos siguientes)."""
    cfg = _cfg(reconnect_backoff_s=[0.0])
    bus = EventBus()
    client = OWWClient(cfg.oww, audio_cfg=cfg.audio, bus=bus)

    async def _always_drop(n: int) -> str:
        raise ConnectionError(f"disconnect #{n}")

    # 6 fallos: el doble del umbral, para comprobar que no hay spam.
    wfd = _CountingWFD(6, _always_drop)

    async with _Collector(bus) as col:
        with _patches(wfd):
            await _run_until(client, wfd)
        await col.drain()

    degraded = col.of_type("oww_degraded")
    assert len(degraded) == 1, (
        f"esperaba exactamente 1 evento oww_degraded tras 3 fallos, "
        f"obtuve {len(degraded)}"
    )
    assert degraded[0].data["consecutive_failures"] == 3


@pytest.mark.asyncio
async def test_run_forever_publishes_oww_recovered_after_successful_reconnect() -> None:
    """Tras 2 fallos, una reconexión exitosa, publicar oww_recovered (1 vez)."""
    cfg = _cfg(oww_healthcheck_threshold=2, reconnect_backoff_s=[0.0])
    bus = EventBus()
    client = OWWClient(cfg.oww, audio_cfg=cfg.audio, bus=bus)

    async def _behaviour(n: int) -> str:
        if n <= 2:
            raise ConnectionError(f"fail #{n}")
        if n == 3:
            return "ok_nabu"  # éxito → activa oww_recovered
        # Tras el éxito, bloqueamos hasta que el test cancele.
        await asyncio.Event().wait()
        return "unreachable"  # pragma: no cover

    # La 4ª llamada solo ocurre después de procesar la detección (y
    # publicar oww_recovered).
    wfd = _CountingWFD(4, _behaviour)

    async with _Collector(bus) as col:
        with _patches(wfd):
            await _run_until(client, wfd)
        await col.drain()

    degraded = col.of_type("oww_degraded")
    recovered = col.of_type("oww_recovered")
    assert len(degraded) == 1, f"esperaba 1 oww_degraded, obtuve {len(degraded)}"
    assert len(recovered) == 1, f"esperaba 1 oww_recovered, obtuve {len(recovered)}"


@pytest.mark.asyncio
async def test_run_forever_no_publish_when_bus_is_none() -> None:
    """Sin bus inyectado, run_forever no publica nada y no lanza — comportamiento
    legacy preservado."""
    cfg = _cfg(reconnect_backoff_s=[0.0])
    client = OWWClient(cfg.oww, audio_cfg=cfg.audio, bus=None)

    async def _always_drop(n: int) -> str:
        raise ConnectionError(f"nope #{n}")

    # Por encima del umbral (3): si el código intentara publicar sin bus,
    # lanzaría AttributeError y run_forever terminaría con error en vez de
    # cancelada.
    wfd = _CountingWFD(5, _always_drop)
    with _patches(wfd):
        await _run_until(client, wfd)
    assert client._degraded_published is False


def test_oww_config_has_default_healthcheck_threshold() -> None:
    """OWWConfig.oww_healthcheck_threshold debe tener default 3."""
    cfg = OWWConfig()
    assert hasattr(cfg, "oww_healthcheck_threshold"), (
        "OWWConfig no tiene oww_healthcheck_threshold — el fix no se aplicó"
    )
    assert cfg.oww_healthcheck_threshold == 3, (
        f"default esperaba 3, obtuve {cfg.oww_healthcheck_threshold}"
    )


@pytest.mark.asyncio
async def test_connect_with_backoff_counts_initial_failures_for_degraded() -> None:
    """Con el servidor OWW caído desde el arranque, connect_with_backoff()
    reintentaba para siempre sin publicar oww_degraded: consecutive_drops
    solo contaba caídas tras conectar. Los fallos de conexión iniciales
    también cuentan para el umbral (una sola publicación)."""
    cfg = _cfg(reconnect_backoff_s=[0.0])
    bus = EventBus()
    client = OWWClient(cfg.oww, audio_cfg=cfg.audio, bus=bus)
    attempts = {"n": 0}

    async def _connect(_self) -> None:
        attempts["n"] += 1
        if attempts["n"] <= 5:
            raise ConnectionRefusedError("OWW caído")

    async with _Collector(bus) as col:
        with patch.object(OWWClient, "connect", autospec=True, side_effect=_connect):
            await asyncio.wait_for(client.connect_with_backoff(), timeout=_SAFETY_TIMEOUT_S)
        await col.drain()

    degraded = col.of_type("oww_degraded")
    assert len(degraded) == 1, degraded
    assert degraded[0].data["consecutive_failures"] == 3
    assert client._degraded_published is True


@pytest.mark.asyncio
async def test_connect_with_backoff_below_threshold_does_not_publish() -> None:
    cfg = _cfg(reconnect_backoff_s=[0.0])
    bus = EventBus()
    client = OWWClient(cfg.oww, audio_cfg=cfg.audio, bus=bus)
    attempts = {"n": 0}

    async def _connect(_self) -> None:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise ConnectionRefusedError("OWW caído")

    async with _Collector(bus) as col:
        with patch.object(OWWClient, "connect", autospec=True, side_effect=_connect):
            await asyncio.wait_for(client.connect_with_backoff(), timeout=_SAFETY_TIMEOUT_S)
        await col.drain()

    assert col.of_type("oww_degraded") == []


def test_registry_make_oww_wires_bus_to_oww_client() -> None:
    """oww_degraded/oww_recovered nunca llegaban en producción: make_oww →
    WyomingBackend → OWWClient no pasaban el bus."""
    from backends import registry

    bus = EventBus()
    backend = registry.make_oww(_cfg(), bus=bus)
    assert backend._client._bus is bus
