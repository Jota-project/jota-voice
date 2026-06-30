# Plan: jota-voice v2 — arquitectura event-driven

**Fecha:** 2026-06-10
**Estado:** aprobado
**Autor:** Alfonso Garre + Claude

---

## Contexto

El prototipo v1 (implementado en sesiones anteriores) demostró la viabilidad del
pipeline básico pero tiene un bug de hang tras la respuesta y carece de la arquitectura
event-driven necesaria para soportar sincronización texto/audio y futura observabilidad.

Se parte de cero con un diseño limpio basado en el spec actualizado en `docs/spec.md`.
El código en `client/` se reescribe completamente.

---

## Objetivo del plan

Implementar la v2 de jota-voice con:
- Bus interno de eventos (`EventBus`)
- Sincronización texto ↔ audio en reproducción
- `DisplayClient` reactivo suscrito al bus
- Máquina de estados robusta sin bugs de hang
- Código limpio y verificable por fases

---

## Fases

### Fase 0 — Limpiar y preparar (1 sesión)

Tareas:
- Reemplazar todos los ficheros en `client/` por sus versiones v2
- Mantener `config.py` (ya es correcto — dataclasses, sin pydantic)
- Mantener `install.sh`, `deploy.sh`, `boot/start.sh` (ya funcionan)
- Mantener `config.yaml` en el teléfono (sin cambios)

Criterio: el repo está limpio, sin código v1 residual.

---

### Fase 1 — Event Bus (½ sesión)

Fichero: `client/event_bus.py`

Implementar `EventBus` con soporte para múltiples suscriptores simultáneos via
`asyncio.Queue`. Cada llamada a `subscribe()` devuelve un iterador async
independiente que recibe todos los eventos publicados desde ese momento.

```python
class EventBus:
    def publish(self, event: VoiceEvent) -> None
    def subscribe(self) -> AsyncIterator[VoiceEvent]
```

También define el dataclass `VoiceEvent` con todos los tipos del spec.

Criterio: test unitario local — un publisher, dos suscriptores, ambos reciben todos
los eventos en orden.

---

### Fase 2 — Audio Capture (½ sesión)

Fichero: `client/audio_capture.py`

Igual que el `AudioIO` v1 en la parte de captura, pero extraído a su propio módulo.
Responsabilidades:
- PyAudio callback → `asyncio.Queue` (float32 bytes)
- Ring-buffer de pre-roll
- `is_silence()` por RMS

Sin reproducción (eso va en `PlaybackEngine`).

Criterio: en el teléfono, capturar 3s de audio y medir RMS — silencio < 200, voz > 200.

---

### Fase 3 — OWW Client (½ sesión)

Fichero: `client/oww_client.py`

Igual que `oww.py` v1. El protocolo Wyoming está bien implementado. Limpiar y
renombrar. Sin cambios funcionales.

Criterio: `ok nabu` detectado en terminal sin arrancar el pipeline completo.

---

### Fase 4 — Gateway Client (½ sesión)

Fichero: `client/gateway_client.py`

Igual que `gateway.py` v1. Renombrar, limpiar. Sin cambios funcionales.

Criterio: enviar un audio de prueba al gateway y recibir `transcription` + tokens
+ al menos un chunk de audio TTS.

---

### Fase 5 — Playback Engine (1 sesión)

Fichero: `client/playback_engine.py`

Este es el módulo nuevo más importante. Responsabilidades:
1. Recibir chunks de audio TTS y reproducirlos en orden via PyAudio (PCM16 24kHz)
2. Para cada chunk, calcular su duración: `len(bytes) / (24000 * 2)` segundos
3. Mantener un `text_cursor` sobre el `text_buffer` acumulado de tokens LLM
4. Avanzar el cursor a `chars_per_second` durante la reproducción de cada chunk
5. Emitir `VoiceEvent(type="display_text_update", data={"text": visible_text})`
   cada ~50ms mientras reproduce

```python
class PlaybackEngine:
    def __init__(self, bus: EventBus, pa: pyaudio.PyAudio)
    def push_token(self, content: str) -> None      # añade al text_buffer
    async def play_chunk(self, audio: bytes) -> None # reproduce + avanza cursor
    async def drain(self) -> None                    # espera fin de reproducción
    def reset(self) -> None                          # limpia estado entre turnos
```

El `text_buffer` es una lista de tokens. El cursor es un índice de caracteres.
La sincronización es aproximada — si no hay suficientes tokens, se muestra lo que hay.

Criterio: reproducir un chunk de audio real del gateway mientras el texto asociado
aparece sincronizado en la pantalla del teléfono.

---

### Fase 6 — Display Client (½ sesión)

Fichero: `client/display_client.py`

Suscriptor del `EventBus`. Traduce `VoiceEvent` a POSTs HTTP a jota-display.

Mapeo de eventos → estados de display:

| VoiceEvent | Estado display | Texto |
|---|---|---|
| `recording_started` | `"listening"` | — |
| `transcription` | `"thinking"` | texto transcrito |
| `playback_started` | `"response"` | — |
| `display_text_update` | `"response"` | texto sincronizado |
| `state_changed("idle")` | `"idle"` | — |

```python
class DisplayClient:
    def __init__(self, cfg: DisplayConfig, bus: EventBus)
    async def run(self) -> None   # loop suscrito al bus, se cancela externamente
```

Criterio: el kiosk refleja cada fase del ciclo sin llamadas directas desde la
máquina de estados.

---

### Fase 7 — State Machine (1-2 sesiones)

Fichero: `client/state_machine.py`

La máquina de estados coordina todos los módulos. Publica en el bus en cada
transición. No hace I/O directamente — todo pasa por los módulos.

```
IDLE:
  - Drena queue de audio stale
  - Publica state_changed("idle")
  - Task A: captura audio → OWW (send_audio en loop)
  - Task B: wait_for_detection()
  - Cuando B completa → cancela A → RECORDING

RECORDING:
  - Publica recording_started + wake_word_detected
  - Conecta a gateway
  - Envía pre-roll + audio nuevo
  - VAD / timeout → send_end → RESPONDING

RESPONDING:
  - Recibe GatewayEvents del WS:
    · transcription → publica VoiceEvent
    · llm_token → PlaybackEngine.push_token() + publica VoiceEvent
    · tts_chunk → PlaybackEngine.play_chunk() + publica VoiceEvent
  - Timeout 30s sin audio → abortar → IDLE
  - WS cierra → PlaybackEngine.drain() → publica playback_ended → IDLE
```

Toda transición de error publica `VoiceEvent(type="error")` antes de ir a IDLE.

Criterio: ciclo completo E2E sin hang — "ok nabu → pregunta → respuesta → IDLE".

---

### Fase 8 — Entry point y arranque (½ sesión)

Fichero: `client/voice_client.py`

Instancia todos los módulos, crea el bus, arranca tareas:
- `DisplayClient.run()` como task background permanente
- `state_machine.run()` como loop principal
- Gestión de señales (SIGTERM → shutdown limpio)

Criterio: `python client/voice_client.py config.yaml` arranca, pasa el ciclo
completo y vuelve a IDLE limpiamente. Boot en frío del teléfono funciona.

---

### Fase 9 — Test E2E y ajuste (1 sesión)

- Medir latencia real: fin de voz → inicio de audio de respuesta (objetivo: < 2s)
- Verificar sincronización texto/audio visualmente
- Parar `wyoming-satellite` y verificar que no se nota la diferencia
- Test de resiliencia: matar OWW → jota-voice reconecta sin reinicio

Criterio: latencia < 2s, sin hangs en 10 ciclos consecutivos.

---

## Dependencias entre fases

```
F0
├── F1 (EventBus)
├── F2 (AudioCapture)     ← independiente de F1
├── F3 (OWWClient)        ← independiente de F1, F2
└── F4 (GatewayClient)    ← independiente de F1, F2, F3

F1 + F4 → F5 (PlaybackEngine)
F1       → F6 (DisplayClient)
F1 + F2 + F3 + F4 + F5 + F6 → F7 (StateMachine)
F7       → F8 (Entry point)
F8       → F9 (Test E2E)
```

F2, F3, F4 son paralelizables entre sí.
F5 y F6 son paralelizables entre sí (ambos dependen solo de F1).

---

## Lo que NO cambia

- `config.py` — ya correcto, sin pydantic
- `install.sh`, `deploy.sh`, `boot/start.sh` — funcionan
- `config.yaml` en el teléfono — sin cambios
- Protocolo OWW, protocolo gateway — sin cambios
- Infraestructura (green-house, worker-01) — sin cambios
