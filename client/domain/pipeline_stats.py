"""pipeline_stats.py — telemetría ``pipeline_event`` del gateway.

jota-gateway (src/services/pipeline_tracker.py, v1.19.x) manda
``{"type": "pipeline_event", "stage", "elapsed_ms", "turn"}`` en cada etapa
del pipeline cuando el handshake pide ``status`` en output_mode. No está en
client-protocol.md; esto se basa en el código del gateway:

- Etapas: transcription_partial, transcription_final,
  transcription_incomplete, llm_start, llm_first_token, llm_done, tts_start,
  tts_first_chunk, tts_done, barge_in, client_slow, session_revoked y
  session_end.
- ``elapsed_ms`` es el tiempo desde el evento ANTERIOR del tracker (de
  cualquier etapa), no desde el inicio del turno.
- ``turn`` es un contador de turnos del tracker, NO el ``turn_id`` ni el
  ``turn_seq`` del protocolo. Además ``transcription_final`` se registra con
  el contador del turno anterior (``start_turn()`` va después), así que no
  sirve para agrupar etapas por turno.

Lo usan ``state_machine._responding`` (turno propio) y
``gateway_session.gateway_idle_listener`` (IDLE).
"""

from __future__ import annotations

import logging
from typing import Any

from .event_bus import EventBus, VoiceEvent

log = logging.getLogger(__name__)

# Merecen aviso: el gateway va a cortar la sesión (cliente que no lee) o la
# ha revocado.
WARNING_STAGES = frozenset({"client_slow", "session_revoked"})

# Etapas del LLM y del TTS. Dentro de NUESTRO turno prueban que el gateway
# trabaja en él (ver state_machine._responding).
TURN_ACTIVITY_STAGES = frozenset({
    "llm_start", "llm_first_token", "llm_done",
    "tts_start", "tts_first_chunk", "tts_done",
})

# Marcas de la línea de latencias, medidas desde transcription_final.
_MARKS = (
    ("llm_first_token", "1º token LLM"),
    ("tts_first_chunk", "1º chunk TTS"),
    ("tts_done", "fin TTS"),
)


def publish_pipeline_stage(bus: EventBus, data: dict[str, Any], where: str) -> None:
    """Publica ``pipeline_stage`` en el bus (por si un display lo quiere).

    Solo ``client_slow`` y ``session_revoked`` dejan rastro (WARNING); el
    resto ya queda resumido en la línea de latencias del turno.
    """
    bus.publish(VoiceEvent(type="pipeline_stage", data=dict(data)))
    stage = data.get("stage")
    if stage in WARNING_STAGES:
        log.warning(
            "%s: el gateway informa %s (turn=%s, elapsed_ms=%s)",
            where, stage, data.get("turn"), data.get("elapsed_ms"),
        )
    else:
        log.debug("%s: pipeline %s elapsed_ms=%s", where, stage, data.get("elapsed_ms"))


class TurnLatency:
    """Latencias de un turno a partir de los ``pipeline_event``.

    Las marcas del gateway se calculan sumando ``elapsed_ms`` desde
    ``transcription_final`` (tiempos del propio gateway, sin el retraso con
    el que el cliente lee el socket mientras reproduce). El fin del turno se
    mide con el reloj del cliente desde que llegó ``transcription_final``.
    """

    def __init__(self) -> None:
        self._acc_ms: float | None = None
        self._marks: dict[str, float] = {}
        self._final_at: float | None = None

    def feed(self, stage: str, elapsed_ms: float | None, *, now: float) -> None:
        if stage == "transcription_final":
            self._acc_ms = 0.0
            self._marks = {}
            self._final_at = now
            return
        if self._acc_ms is None:
            return  # etapas previas al STT final (parciales, otro turno)
        if elapsed_ms is not None:
            self._acc_ms += elapsed_ms
        self._marks.setdefault(stage, self._acc_ms)

    def summary(self, *, now: float) -> str | None:
        """Línea de latencias, o None si no se vio ``transcription_final``."""
        if self._acc_ms is None or self._final_at is None:
            return None
        parts = []
        for stage, label in _MARKS:
            value = self._marks.get(stage)
            parts.append(f"{label} {value:.0f}" if value is not None else f"{label} —")
        end_ms = (now - self._final_at) * 1000
        return (
            "Latencias del turno (ms desde STT final): "
            + " → ".join(parts)
            + f" | fin turno {end_ms:.0f} (cliente)"
        )
