"""
test_event_bus.py — Test unitario para EventBus y VoiceEvent.

Escenario: 1 publisher, 2 suscriptores simultáneos.
Ambos deben recibir todos los eventos en el mismo orden.
"""

import asyncio

from domain.event_bus import EventBus, VoiceEvent


async def _run_test() -> None:
    bus = EventBus()

    # Eventos que se publicarán
    events_to_publish = [
        VoiceEvent(type="wake_word_detected", data={"confidence": 0.99}),
        VoiceEvent(type="recording_started", data={}),
        VoiceEvent(type="recording_ended", data={"duration": 3.2}),
        VoiceEvent(type="transcription_partial", data={"text": "hola"}),
        VoiceEvent(type="transcription", data={"text": "hola jota"}),
        VoiceEvent(type="llm_token", data={"token": "Buenos"}),
        VoiceEvent(type="tts_chunk", data={"audio_chunk": b"pcm_data"}),
        VoiceEvent(type="playback_started", data={}),
        VoiceEvent(type="playback_ended", data={}),
        VoiceEvent(type="state_changed", data={"from": "SPEAKING", "to": "IDLE"}),
        VoiceEvent(type="display_text_update", data={"text": "¿En qué te ayudo?"}),
        VoiceEvent(type="error", data={"message": "Audio device error", "code": "AUDIO_ERR"}),
    ]

    received_a: list[VoiceEvent] = []
    received_b: list[VoiceEvent] = []

    async def subscriber_a() -> None:
        async for event in bus.subscribe():
            received_a.append(event)

    async def subscriber_b() -> None:
        async for event in bus.subscribe():
            received_b.append(event)

    async def publisher() -> None:
        # Pequeño yield para que los suscriptores se registren primero
        await asyncio.sleep(0)
        for ev in events_to_publish:
            bus.publish(ev)
        # Pequeño yield para que los suscriptores procesen todos los eventos
        await asyncio.sleep(0)
        bus.close()

    # Lanzar suscriptores y publisher como tareas concurrentes
    task_a = asyncio.create_task(subscriber_a())
    task_b = asyncio.create_task(subscriber_b())
    await publisher()
    await asyncio.gather(task_a, task_b)

    # --- Verificaciones ---
    n = len(events_to_publish)

    assert len(received_a) == n, (
        f"Suscriptor A recibió {len(received_a)} eventos, esperaba {n}"
    )
    assert len(received_b) == n, (
        f"Suscriptor B recibió {len(received_b)} eventos, esperaba {n}"
    )

    for i, (ev_pub, ev_a, ev_b) in enumerate(
        zip(events_to_publish, received_a, received_b)
    ):
        assert ev_a is ev_pub, (
            f"Evento {i}: suscriptor A recibió objeto diferente (type={ev_a.type})"
        )
        assert ev_b is ev_pub, (
            f"Evento {i}: suscriptor B recibió objeto diferente (type={ev_b.type})"
        )
        assert ev_a.type == events_to_publish[i].type, (
            f"Evento {i}: orden incorrecto en A — got={ev_a.type}, "
            f"expected={events_to_publish[i].type}"
        )
        assert ev_b.type == events_to_publish[i].type, (
            f"Evento {i}: orden incorrecto en B — got={ev_b.type}, "
            f"expected={events_to_publish[i].type}"
        )

    print(f"OK — {n} eventos publicados, 2 suscriptores, todos en orden correcto.")
    print("Tipos recibidos:")
    for ev in received_a:
        print(f"  {ev.type}")


def test_event_bus() -> None:
    asyncio.run(_run_test())


def test_subscribe_registra_al_llamar_no_al_iterar() -> None:
    """Integración Fase D + Fase 7: los status que llegan antes de `ready`
    durante el connect inicial se publicaban antes de que los consumidores
    empezaran a iterar y se perdían. subscribe() registra la cola en el
    momento de la llamada; aclose() la retira aunque no se haya iterado."""

    async def _run() -> None:
        bus = EventBus()
        sub = bus.subscribe()
        bus.publish(VoiceEvent(type="gateway_status", data={"state": "unavailable"}))
        ev = await asyncio.wait_for(sub.__anext__(), 1.0)
        assert ev.type == "gateway_status"
        await sub.aclose()
        assert bus._queues == []

        never_iterated = bus.subscribe()
        assert len(bus._queues) == 1
        await never_iterated.aclose()
        assert bus._queues == []

    asyncio.run(_run())


def test_voice_event_acepta_tipos_nuevos_issue_24():
    """Issue #24: VoiceEvent admite los tipos nuevos sin mypy complaint."""
    for event_type in ("gateway_ready", "interrupted", "turn_start", "tool_call"):
        ev = VoiceEvent(type=event_type, data={"x": 1})  # type: ignore[arg-type]
        assert ev.type == event_type


# ---------------------------------------------------------------------------
# Issue #63: un suscriptor que lanza no mata su bucle, ni a los demás, ni la
# sesión
# ---------------------------------------------------------------------------

import logging  # noqa: E402

import pytest  # noqa: E402

from domain.event_bus import consume  # noqa: E402


def _ev(type_: str) -> VoiceEvent:
    return VoiceEvent(type=type_, data={})  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("async_handler", [False, True])
async def test_consume_registra_la_excepcion_y_sigue(caplog, async_handler) -> None:
    bus = EventBus()
    seen: list[str] = []
    healthy: list[str] = []

    def _sync(ev):
        seen.append(ev.type)
        if ev.type == "error":
            raise RuntimeError("backend roto")

    async def _async(ev):
        _sync(ev)

    handler = _async if async_handler else _sync
    bad = asyncio.create_task(consume(bus.subscribe(), handler, name="defectuoso"))
    good = asyncio.create_task(consume(bus.subscribe(), lambda ev: healthy.append(ev.type), name="sano"))
    with caplog.at_level(logging.ERROR, logger="domain.event_bus"):
        for t in ("recording_started", "error", "playback_ended"):
            bus.publish(_ev(t))
        bus.close()
        await asyncio.wait_for(asyncio.gather(bad, good), 1.0)
    assert seen == ["recording_started", "error", "playback_ended"]
    assert healthy == seen
    assert "defectuoso" in caplog.text and "backend roto" in caplog.text


def _always_fails(ev):
    raise RuntimeError(f"roto {ev.type}")


async def _drive(handler, n: int, **kw) -> None:
    bus = EventBus()
    t = asyncio.create_task(consume(bus.subscribe(), handler, name="roto", **kw))
    for _ in range(n):
        bus.publish(_ev("recording_started"))
    bus.close()
    await asyncio.wait_for(t, 1.0)


@pytest.mark.asyncio
async def test_consume_traceback_solo_en_el_primer_fallo_y_luego_resumen_cada_n(caplog) -> None:
    """Revisión: un backend roto de forma permanente volcaba un traceback por
    evento y llenaba el log (Termux)."""
    with caplog.at_level(logging.DEBUG, logger="domain.event_bus"):
        await _drive(_always_fails, 250, summary_every=100, summary_interval_s=3600)
    with_tb = [r for r in caplog.records if r.exc_info]
    summaries = [r for r in caplog.records if not r.exc_info and r.levelno >= logging.WARNING]
    assert len(with_tb) == 1
    assert len(summaries) == 2  # a los 100 y a los 200 fallos
    assert "100" in summaries[0].getMessage() and "200" in summaries[1].getMessage()


@pytest.mark.asyncio
async def test_consume_resumen_por_tiempo_con_reloj_falso(caplog) -> None:
    now = [0.0]

    def _handler(ev):
        now[0] += 10.0  # cada fallo "tarda" 10 s de reloj falso
        raise RuntimeError("roto")

    with caplog.at_level(logging.DEBUG, logger="domain.event_bus"):
        await _drive(_handler, 7, summary_every=1000, summary_interval_s=30, clock=lambda: now[0])
    summaries = [r for r in caplog.records if not r.exc_info and r.levelno >= logging.WARNING]
    # Primer fallo en t=10 con traceback; resúmenes en t=40 y t=70.
    assert len(summaries) == 2


@pytest.mark.asyncio
async def test_consume_retira_la_suscripcion_al_terminar_o_cancelarse() -> None:
    bus = EventBus()
    t = asyncio.create_task(consume(bus.subscribe(), lambda ev: None, name="x"))
    await asyncio.sleep(0)
    assert len(bus._queues) == 1
    t.cancel()
    await asyncio.wait([t])
    assert t.cancelled(), "consume no debe tragarse la cancelación"
    assert bus._queues == []


@pytest.mark.asyncio
async def test_menubar_client_sobrevive_a_un_backend_que_lanza() -> None:
    """MenubarClient.run no tenía try/except alrededor de _handle: un backend
    defectuoso mataba la task del menubar para el resto de la sesión."""
    import queue

    from ui.menubar_base import MenubarBackend
    from ui.menubar_client import MenubarClient

    from unittest.mock import MagicMock

    backend = MagicMock(spec=MenubarBackend)
    backend.set_status_text.side_effect = [RuntimeError("Cocoa roto"), None]
    client = MenubarClient(backend)
    bus = EventBus()
    events = bus.subscribe()
    loop = asyncio.get_running_loop()
    t = asyncio.create_task(client.run(bus, queue.Queue(), asyncio.Event(), loop, asyncio.Event(), events))
    bus.publish(VoiceEvent(type="transcription", data={"text": "uno"}))
    bus.publish(VoiceEvent(type="transcription", data={"text": "dos"}))
    bus.close()
    await asyncio.wait_for(t, 1.0)
    assert backend.set_status_text.call_count == 2
    backend.set_status_text.assert_called_with("dos")
