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
