"""
test_event_bus.py — Test unitario para EventBus y VoiceEvent.

Escenario: 1 publisher, 2 suscriptores simultáneos.
Ambos deben recibir todos los eventos en el mismo orden.
"""

import asyncio
import sys
import os

# Permite ejecutar desde cualquier directorio
sys.path.insert(0, os.path.dirname(__file__))

from event_bus import EventBus, VoiceEvent


async def _run_test() -> None:
    bus = EventBus()

    # Eventos que se publicarán
    events_to_publish = [
        VoiceEvent(type="wake_word_detected", data={"confidence": 0.99}),
        VoiceEvent(type="recording_started", data={}),
        VoiceEvent(type="transcription_partial", data={"text": "hola"}),
        VoiceEvent(type="transcription", data={"text": "hola jota"}),
        VoiceEvent(type="llm_token", data={"token": "Buenos"}),
        VoiceEvent(type="playback_started", data={}),
        VoiceEvent(type="playback_ended", data={}),
        VoiceEvent(type="state_changed", data={"from": "SPEAKING", "to": "IDLE"}),
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
    """Entry point compatible con pytest y ejecución directa."""
    asyncio.run(_run_test())


if __name__ == "__main__":
    test_event_bus()
