"""
test_display_client.py — Test offline del mapeo VoiceEvent → estado display.

Sin HTTP real: se mockea el backend para capturar las llamadas a update().
Compatible con pytest y ejecución directa.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from event_bus import EventBus, VoiceEvent
from display_client import DisplayClient


async def _run_test() -> None:
    bus = EventBus()

    calls: list[tuple[str, dict]] = []

    class FakeBackend:
        async def update(self, state: str, **kwargs) -> None:
            calls.append((state, kwargs))

    client = DisplayClient(FakeBackend())

    # (evento, estado_esperado_o_None, texto_esperado_o_None)
    test_cases = [
        (VoiceEvent(type="recording_started", data={}),                           "listening", ""),
        (VoiceEvent(type="transcription", data={"text": "hola jota"}),            "thinking",  "hola jota"),
        (VoiceEvent(type="playback_started", data={}),                            "response",  ""),
        (VoiceEvent(type="display_text_update", data={"text": "Buenos días"}),    "response",  "Buenos días"),
        # state_changed con formato {"state": "idle"} (la nueva API solo mira "state")
        (VoiceEvent(type="state_changed", data={"from": "SPEAKING", "to": "IDLE"}), None, None),
        # state_changed con formato canónico {"state": "idle"}
        (VoiceEvent(type="state_changed", data={"state": "idle"}),                "idle",      ""),
        # --- Eventos que deben ignorarse (sin update) ---
        (VoiceEvent(type="wake_word_detected", data={"confidence": 0.99}),        None, None),
        (VoiceEvent(type="recording_ended", data={}),                             None, None),
        (VoiceEvent(type="transcription_partial", data={"text": "ho"}),           None, None),
        (VoiceEvent(type="llm_token", data={"token": "Buenos"}),                  None, None),
        (VoiceEvent(type="tts_chunk", data={"audio_chunk": b"pcm"}),              None, None),
        (VoiceEvent(type="playback_ended", data={}),                              None, None),
        (VoiceEvent(type="error", data={"message": "err"}),                       None, None),
        # state_changed a un estado que NO es idle → ignorado
        (VoiceEvent(type="state_changed", data={"from": "IDLE", "to": "RECORDING"}), None, None),
    ]

    expected_calls = []
    for _, state, text in test_cases:
        if state is not None:
            kwargs = {"text": text} if text else {}
            expected_calls.append((state, kwargs))

    for ev, _, _ in test_cases:
        await client._handle(ev)

    assert calls == expected_calls, (
        f"Mapeo incorrecto.\nEsperado: {expected_calls}\nObtenido:  {calls}"
    )

    n_ignored = len(test_cases) - len(expected_calls)
    print(f"OK — {len(expected_calls)} updates generados, {n_ignored} eventos ignorados.")
    print("Updates enviados:")
    for state, kwargs in calls:
        t = f' text={kwargs["text"]!r}' if kwargs.get("text") else ""
        print(f"  state={state!r}{t}")


def test_display_client_mapping() -> None:
    """Entry point compatible con pytest y ejecución directa."""
    asyncio.run(_run_test())


if __name__ == "__main__":
    test_display_client_mapping()