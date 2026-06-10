"""
test_display_client.py — Test offline del mapeo VoiceEvent → estado display.

Sin HTTP real: se parchea _post para capturar las llamadas.
Compatible con pytest y ejecución directa.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from config import DisplayConfig
from event_bus import EventBus, VoiceEvent
from display_client import DisplayClient


async def _run_test() -> None:
    cfg = DisplayConfig(url="http://127.0.0.1:9999", timeout_s=1.0)
    bus = EventBus()
    client = DisplayClient(cfg, bus)

    results: list[tuple[str, str]] = []

    async def fake_post(state: str, text: str = "") -> None:
        results.append((state, text))

    client._post = fake_post  # type: ignore[method-assign]

    # (evento, estado_esperado_o_None, texto_esperado_o_None)
    test_cases = [
        (VoiceEvent(type="recording_started", data={}),                           "listening", ""),
        (VoiceEvent(type="transcription", data={"text": "hola jota"}),            "thinking",  "hola jota"),
        (VoiceEvent(type="playback_started", data={}),                            "response",  ""),
        (VoiceEvent(type="display_text_update", data={"text": "Buenos días"}),    "response",  "Buenos días"),
        # state_changed con formato {"to": "IDLE"} (usado por state machine real)
        (VoiceEvent(type="state_changed", data={"from": "SPEAKING", "to": "IDLE"}), "idle",   ""),
        # state_changed con formato alternativo {"state": "idle"}
        (VoiceEvent(type="state_changed", data={"state": "idle"}),                "idle",      ""),
        # --- Eventos que deben ignorarse (sin POST) ---
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

    expected_posts = [(s, t) for (_, s, t) in test_cases if s is not None]

    for ev, _, _ in test_cases:
        await client._handle(ev)

    assert results == expected_posts, (
        f"Mapeo incorrecto.\nEsperado: {expected_posts}\nObtenido:  {results}"
    )

    n_ignored = len(test_cases) - len(expected_posts)
    print(f"OK — {len(expected_posts)} POSTs generados, {n_ignored} eventos ignorados.")
    print("POSTs enviados:")
    for state, text in results:
        t = f' text="{text}"' if text else ""
        print(f"  state={state!r}{t}")


def test_display_client_mapping() -> None:
    """Entry point compatible con pytest y ejecución directa."""
    asyncio.run(_run_test())


if __name__ == "__main__":
    test_display_client_mapping()
