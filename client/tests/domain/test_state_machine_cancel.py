"""Tests de cancelación de tasks anidados — Issue #21.

Replica el bug: cuando la tarea externa que ejecuta _recording /
_responding es cancelada desde fuera (SIGTERM durante shutdown), los
tasks hijos creados con asyncio.create_task() deben quedar cancelados Y
drenados antes de que la tarea externa retorne.

El patrón correcto ya existe en _wait_wake_or_cancel (try/finally tras
asyncio.wait). Estos tests verifican el mismo contrato en _recording y
_responding, replicado al aplicar el fix de la issue.

Nota: _oww_loop vive en client/app/voice_client.py (no en state_machine),
así que se excluye de la parametrización; el fix se aplica allí manualmente
en la misma tarea (ver diff).
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import AudioConfig, Config, DeviceConfig, GatewayConfig
from domain.event_bus import EventBus


# ---------------------------------------------------------------------------
# Helpers de mock (mismo patrón que test_state_machine.py)
# ---------------------------------------------------------------------------

def _cfg(recording_timeout_s: float = 60.0) -> Config:
    return Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="x"),
        device=DeviceConfig(id="test"),
        audio=AudioConfig(
            sample_rate=16000,
            frames_per_buffer=512,
            silence_timeout_s=0.1,
            recording_timeout_s=recording_timeout_s,
        ),
    )


def _audio_mock() -> MagicMock:
    """Audio mock: get_queue() devuelve una Queue vacía (capture_task se queda
    en wait_for(q.get(), timeout=0.1) indefinidamente hasta que el bucle de
    prueba lo cancele). recording_timeout_s alto (60s) garantiza que la única
    vía de salida del capture_task es la cancelación explícita."""
    audio = MagicMock()
    q: asyncio.Queue[bytes] = asyncio.Queue()  # vacía
    audio.get_queue.return_value = q
    audio.get_preroll.return_value = b""
    audio.is_silence.return_value = False
    return audio


def _gateway_mock() -> MagicMock:
    gateway = MagicMock()
    gateway.connect = AsyncMock()
    gateway.disconnect = AsyncMock()
    gateway.send_audio = AsyncMock()
    gateway.send_end = AsyncMock()
    gateway.send_text = AsyncMock()
    gateway.send_cancel = AsyncMock()
    return gateway


def _playback_mock() -> MagicMock:
    playback = MagicMock()
    playback.push_token = MagicMock()
    playback.play_chunk = AsyncMock()
    playback.play_notification = AsyncMock()
    playback.drain = AsyncMock()
    playback.reset = MagicMock()
    return playback


def _blocking_receive_gen():
    """Generador async que bloquea indefinidamente — usado por _responding
    para que receive_task no termine por sí solo."""
    async def _gen():
        await asyncio.Event().wait()  # bloquea para siempre
        if False:  # noqa: pragma: no cover  — necesario para que sea async gen
            yield
    return _gen()


# ---------------------------------------------------------------------------
# Tests parametrizados para _recording y _responding
# ---------------------------------------------------------------------------

def _recording_coro_factory(
    cfg: Config, bus: EventBus, audio: Any, gateway: Any,
    playback: Any, cancel_event: asyncio.Event,
) -> Awaitable[None]:
    from domain.state_machine import _recording
    return _recording("wake", bus, audio, gateway, playback, cfg, cancel_event)


def _responding_coro_factory(
    cfg: Config, bus: EventBus, audio: Any, gateway: Any,
    playback: Any, cancel_event: asyncio.Event,
) -> Awaitable[None]:
    from domain.state_machine import _responding
    gateway.receive = _blocking_receive_gen
    return _responding(bus, gateway, playback, cancel_event)


@pytest.mark.parametrize(
    "label,coro_factory",
    [
        ("_recording", _recording_coro_factory),
        ("_responding", _responding_coro_factory),
    ],
)
def test_no_orphaned_child_tasks_after_external_cancel(
    label: str, coro_factory: Callable,
) -> None:
    """Issue #21: cuando la tarea externa recibe cancel() durante asyncio.wait,
    los tasks hijos deben quedar drenados antes de que la tarea externa retorne.

    Sin el fix try/finally, los hijos (capture_task, cancel_task, receive_task,
    wake_task) quedan huérfanos tras outer.cancel() — corren hasta
    recording_timeout_s (60s) sin que nadie los await-e. Esto es exactamente
    lo que pasa en producción cuando SIGTERM dispara shutdown durante un turno.

    Verificación: tras outer.cancel() y asyncio.sleep(0.3) (suficiente para
    que asyncio propague cancelaciones, pero muy inferior a
    recording_timeout_s=60s), NO debe haber tasks pendientes distintas a la
    tarea externa (que ya está done) y a la main task.
    """

    async def _driver() -> None:
        cfg = _cfg()
        bus = EventBus()
        audio = _audio_mock()
        gateway = _gateway_mock()
        playback = _playback_mock()
        cancel_event = asyncio.Event()

        outer = asyncio.create_task(
            coro_factory(cfg, bus, audio, gateway, playback, cancel_event)
        )
        # Snapshot DESPUÉS de crear outer pero ANTES de que asyncio.sleep ceda
        # el control y los tasks hijos se creen. Así capturamos solo [main, outer]
        # y todo lo creado después (capture_task, cancel_task, etc.) se detecta
        # como nuevo.
        initial_tasks = set(asyncio.all_tasks())

        # Dejar que asyncio.wait entre en espera (los tasks hijos ya están creados)
        await asyncio.sleep(0.05)
        assert not outer.done(), (
            f"{label}: la tarea externa terminó antes de poder cancelarla"
        )

        outer.cancel()
        try:
            await asyncio.wait_for(outer, timeout=2.0)
        except asyncio.TimeoutError:
            pytest.fail(
                f"{label}: la tarea externa no terminó en 2s tras cancel()"
            )
        except asyncio.CancelledError:
            pass  # comportamiento esperado tras outer.cancel()

        # Esperar para que el bloque finally (si existe) pueda cancelar/drenar.
        # 0.3s es mucho mayor que el tiempo que tarda asyncio en propagar
        # CancelledError, pero muy inferior a recording_timeout_s=60s.
        for _ in range(3):
            await asyncio.sleep(0)
        await asyncio.sleep(0.3)

        # Cualquier task creada durante el test que siga not done es huérfana
        main_task = asyncio.current_task()
        all_now = set(asyncio.all_tasks())
        newly = all_now - initial_tasks
        orphans = [
            t for t in newly
            if not t.done() and t is not outer and t is not main_task
        ]

        assert not orphans, (
            f"{label}: {len(orphans)} task(s) huérfana(s) tras cancel externo "
            f"(issue #21 — fix try/finally no aplicado o aplicado incompleto). "
            f"Tasks: {[t for t in orphans]}"
        )

    asyncio.run(_driver())
