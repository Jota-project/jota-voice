# jota-voice — Especificación técnica

## Visión

jota-voice es la capa de dispositivo del ecosistema jota. Corre en un satélite físico
(teléfono Android, tablet, ESP32...) y convierte cualquier hardware con micrófono y
altavoz en un nodo de voz de primera clase.

Su diferencia frente a un asistente de voz convencional es que **todo es observable en
tiempo real**: el usuario ve cada fase del pipeline — lo que dijo, cómo se transcribió,
qué tokens generó el LLM, cuándo empieza a sonar la voz — a medida que ocurre, no
cuando termina.

El pipeline es enteramente streaming: audio → STT parcial → tokens LLM → chunks TTS.
El texto y el audio se reciben en paralelo; jota-voice los sincroniza para que la
pantalla muestre las palabras exactamente cuando se están pronunciando.

---

## Principios arquitectónicos

1. **Event-driven por dentro.** Toda transición de estado y todo dato recibido se
   publica en un bus interno de eventos. Los consumidores (display, logs, futuro servidor
   de observabilidad) suscriben a ese bus. Añadir un nuevo consumidor no requiere tocar
   la lógica del pipeline.

2. **Streams primero.** El diseño gira alrededor de datos que fluyen, no de
   petición-respuesta. Cada módulo produce o consume un stream; la coordinación ocurre
   en la capa de estados, no dentro de los módulos.

3. **Independencia de hardware.** jota-voice en Android es la implementación de
   referencia. Los módulos de audio son la única parte hardware-específica; el resto es
   agnóstico. Un ESP32 puede implementar el mismo contrato con firmware propio.

4. **Fallo ruidoso, recuperación silenciosa.** Los errores se loguean siempre. La
   máquina de estados vuelve a IDLE en cualquier error sin necesitar reinicio del
   proceso.

5. **Sin estado global mutable.** El estado del sistema vive en la máquina de estados.
   Los módulos son sin estado (reciben lo que necesitan como parámetros o colas).

---

## Arquitectura interna

```
┌─────────────────────────────────────────────────────────────────┐
│                        jota-voice proceso                        │
│                                                                  │
│  ┌──────────┐   PCM int16   ┌─────────┐  Detection  ┌────────┐  │
│  │  Capture │ ────────────► │   OWW   │ ──────────► │        │  │
│  │  (async  │               │ client  │             │ State  │  │
│  │ callback)│ ──────────►   └─────────┘             │Machine │  │
│  └──────────┘  float32                              │        │  │
│       │        queue                                │        │  │
│       │                ┌────────────┐  GatewayEvent │        │  │
│       └──────────────► │  Gateway   │ ─────────────►│        │  │
│                        │  client    │               └───┬────┘  │
│                        └────────────┘                   │       │
│                                                         │       │
│                              ┌──────────────────────────┘       │
│                              │  VoiceEvent stream               │
│                              ▼                                   │
│                     ┌─────────────────┐                         │
│                     │   Event Bus     │                         │
│                     └────────┬────────┘                         │
│                              │                                   │
│              ┌───────────────┼──────────────────┐               │
│              ▼               ▼                  ▼               │
│        ┌──────────┐   ┌────────────┐   ┌─────────────────┐     │
│        │ Display  │   │  Playback  │   │ [futuro: WS obs] │     │
│        │ client   │   │  engine    │   └─────────────────┘     │
│        └──────────┘   └────────────┘                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## Eventos internos

Toda la comunicación entre la máquina de estados y los consumidores ocurre a través de
`VoiceEvent`. El bus es una cola asyncio con múltiples suscriptores.

```python
@dataclass
class VoiceEvent:
    type: Literal[
        # Ciclo de vida
        "wake_word_detected",   # OWW detectó wake word
        "recording_started",    # empieza a grabar
        "recording_ended",      # VAD o timeout → fin de grabación
        # Pipeline de respuesta
        "transcription_partial",# STT parcial (mientras graba)
        "transcription",        # STT final (texto completo)
        "llm_token",            # token del LLM
        "tts_chunk",            # chunk de audio TTS recibido
        # Reproducción
        "playback_started",     # primer chunk de audio reproducido
        "playback_ended",       # reproducción terminada
        # Sistema
        "state_changed",        # transición IDLE/RECORDING/RESPONDING
        "error",                # cualquier error recuperable
    ]
    data: dict          # payload específico del tipo
    ts: float           # timestamp monotónico
```

---

## Máquina de estados

Tres estados. Las transiciones son siempre síncronas; la concurrencia ocurre
**dentro** de cada estado mediante tasks asyncio.

```
┌──────────────────────────────────────────────┐
│                    IDLE                      │
│  Task A: captura audio → OWW (continuo)      │
│  Ring-buffer de pre-roll activo              │
│  Event bus: state_changed("idle")            │
└──────────────────┬───────────────────────────┘
                   │ wake_word_detected
                   ▼
┌──────────────────────────────────────────────┐
│                 RECORDING                    │
│  Conectar a jota-gateway (WS)                │
│  Enviar handshake + pre-roll + audio nuevo   │
│  VAD → silence_timeout → send "end"          │
│  Timeout absoluto → send "end"               │
│  Event bus: recording_started / _ended       │
└──────────────────┬───────────────────────────┘
                   │ "end" enviado
                   ▼
┌──────────────────────────────────────────────┐
│                RESPONDING                    │
│  Recibe GatewayEvents del WS:                │
│   · transcription_partial/final → bus        │
│   · llm_token → bus + text buffer            │
│   · tts_chunk → bus + playback queue         │
│  Playback engine reproduce audio             │
│  Sync engine coordina texto ↔ audio          │
│  Timeout 30s sin audio → abortar             │
└──────────────────┬───────────────────────────┘
                   │ WS cerrado
                   ▼
                  IDLE
```

**Transiciones de error (cualquier estado → IDLE):**
- OWW desconectado → reconnect con backoff, permanecer en IDLE
- Gateway no disponible → log + IDLE
- Timeout RESPONDING → log + IDLE
- Excepción no capturada → log + IDLE

---

## Sincronización texto / audio

Este es el mecanismo central de jota-voice. Los tokens LLM llegan antes que el audio
TTS; la pantalla tiene que mostrar el texto sincronizado con la voz.

### Cómo funciona

1. Los tokens LLM se acumulan en un `text_buffer` en orden de llegada.
2. Cada chunk de audio TTS corresponde a un fragmento del texto ya generado.
3. Conociendo la duración del chunk de audio (`len(bytes) / (24000 * 2)` segundos) y
   el número de caracteres que cubre, calculamos una velocidad de revelado:
   `chars_per_second = chars_in_chunk / audio_duration_seconds`.
4. El `PlaybackEngine` avanza un cursor sobre el `text_buffer` al ritmo calculado
   mientras reproduce cada chunk.
5. En cada tick (~50ms) emite un evento `display_text_update` con el texto visible
   hasta ese cursor.

### Resultado visible

El texto aparece palabra a palabra sincronizado con la voz. El usuario lee y escucha
al mismo tiempo, sin desfase perceptible.

### Fallback

Si la correlación no es posible (e.g. el gateway no envía suficientes tokens antes del
audio), el texto aparece tan pronto llega y el audio suena sin animación.

---

## Módulos

### `event_bus.py`

Bus de eventos asyncio con soporte para múltiples suscriptores.

```python
class EventBus:
    def publish(self, event: VoiceEvent) -> None
    def subscribe(self) -> AsyncIterator[VoiceEvent]
    # Internamente: asyncio.Queue por suscriptor
```

### `audio_capture.py`

Captura PCM int16 de PulseAudio via PyAudio con callback asíncrono.

```python
class AudioCapture:
    async def start(self) -> None
    async def stop(self) -> None
    def get_queue(self) -> asyncio.Queue[bytes]   # float32 bytes
    def get_preroll(self) -> bytes                 # float32, últimos N segundos
    def is_silence(self, frame: bytes) -> bool     # VAD por RMS
```

Mantiene un ring-buffer de pre-roll (`collections.deque(maxlen=N)`).
Conversión: `np.frombuffer(data, np.int16).astype(np.float32) / 32768.0`.

### `oww_client.py`

Protocolo Wyoming implementado a mano (JSON-lines sobre TCP).

```python
class OWWClient:
    async def connect(self) -> None
    async def disconnect(self) -> None
    async def send_audio(self, pcm_int16: bytes) -> None
    async def wait_for_detection(self) -> str     # nombre del wake word
    async def connect_with_backoff(self) -> None
    @property
    def is_connected(self) -> bool
```

### `gateway_client.py`

Cliente WebSocket para `jota-gateway /ws/stream`.

```python
class GatewayClient:
    async def connect(self) -> None
    async def disconnect(self) -> None
    async def send_audio(self, float32_bytes: bytes) -> None
    async def send_end(self) -> None
    def receive(self) -> AsyncIterator[GatewayEvent]
```

### `playback_engine.py`

Reproduce audio PCM16 24kHz y coordina el cursor de texto.

```python
class PlaybackEngine:
    async def play(self, audio: bytes, text_chars: int) -> None
    # Emite display_text_update events cada ~50ms via EventBus
    async def drain(self) -> None
```

### `display_client.py`

Suscriptor del bus. Traduce `VoiceEvent` a POSTs HTTP a jota-display.

```python
class DisplayClient:
    async def run(self) -> None   # loop suscrito al bus
```

### `state_machine.py`

Coordina todos los módulos. Único lugar con lógica de negocio.

```python
async def run(cfg: Config, bus: EventBus) -> None
async def idle(ctx: Context) -> State
async def recording(ctx: Context) -> State
async def responding(ctx: Context) -> State
```

### `config.py`

Dataclasses + YAML. Sin Pydantic (no compila en ARM sin Rust).

---

## Configuración

```yaml
gateway:
  host: "192.168.1.106"
  port: 8004
  path: "/ws/stream"
  client_key: "RELLENAR"
  connect_timeout_s: 10

oww:
  host: "127.0.0.1"
  port: 10401
  wake_words: ["ok_nabu"]
  reconnect_backoff_s: [5, 10, 20, 60]

audio:
  sample_rate: 16000
  channels: 1
  frames_per_buffer: 512
  preroll_seconds: 1.5
  silence_timeout_s: 1.5
  recording_timeout_s: 15.0
  vad_rms_threshold: 200

display:
  url: "http://127.0.0.1:8766"
  timeout_s: 2.0

logging:
  level: "INFO"
```

---

## Dependencias Python

| Paquete | Instalación en Termux ARM |
|---|---|
| `numpy` | `pkg install python-numpy` |
| `portaudio` | `pkg install portaudio` |
| `pyaudio` | `pip install pyaudio` (compila rápido contra portaudio) |
| `websockets` | `pip install websockets` |
| `pyyaml` | `pip install pyyaml` |

Sin Pydantic — usar dataclasses stdlib.

---

## Decisiones de diseño fijadas

| ID | Decisión |
|---|---|
| D1 | Venv propio con `--system-site-packages` |
| D2 | Audio TTS: PCM16 mono 24kHz sin header |
| D3 | Sin barge-in en v1. OWW se pausa durante reproducción |
| D4 | VAD por RMS. `webrtcvad` como mejora futura |
| D5 | Cliente DB: `jota-voice-phone`, key `e8e6feac-d044-4cbf-9dca-2bb074887055` |
| D6 | wyoming-satellite coexiste temporalmente |
| D7 | Sin Pydantic — dataclasses stdlib (Rust no disponible en ARM) |
| D8 | Event bus interno como asyncio Queues — no librería externa |

---

## Fuera de alcance (v1)

- Coordinación entre dispositivos
- Servidor de observabilidad externo (API reactiva)
- Barge-in
- Wake word local en el dispositivo (siempre en worker-01)
- Implementación ESP32
