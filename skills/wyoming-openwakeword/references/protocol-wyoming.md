# Protocolo Wyoming 1.x (lo justo para OWW)

Wyoming es un protocolo **peer-to-peer sobre TCP** que usa **JSON-lines**
(un JSON por línea, `\n` como delimitador). Cada mensaje tiene esta forma:

```json
{ "type": "...", "data": { ... }, "data_length": ..., "payload_length": ... }
```

Opcionalmente seguido de:
- `data_length` bytes de datos extra en UTF-8.
- `payload_length` bytes de payload binario (típicamente PCM de audio).

No hay handshake formal más allá de abrir la conexión TCP.

## Handshake cliente→servidor (opcional, para Wyoming genérico)

Para OWW concreto no es necesario, pero el protocolo completo lo define:

1. Cliente → Servidor: `describe`.
2. Servidor → Cliente: `info` listando servicios disponibles (`asr`, `tts`,
   `wake`, `handle`, etc.).
3. Cliente → Servidor (opcional): `select-program` con `name` para elegir
   entre varios programas del mismo tipo.

OWW concreto solo soporta `wake`, así que en la práctica el cliente salta
directamente al handshake de wake.

## Eventos wake word

| Evento | Dirección | Payload | Notas |
|---|---|---|---|
| `detect` | cliente → servidor | `{"names": ["hey_jarvis"]}` | **OBLIGATORIO** antes de `audio-start`. Sin él, los detectores no se instancian. |
| `audio-start` | cliente → servidor | `{"rate": 16000, "width": 2, "channels": 1}` | Abre el stream PCM. |
| `audio-chunk` | cliente → servidor | mismos campos + payload binario | Cada chunk de audio. |
| `audio-stop` | cliente → servidor | (vacío) | Cierra el stream. |
| `detection` | servidor → cliente | `{"name": "hey_jarvis_v0.1", "timestamp": ...}` | Match. Nombre completo del modelo. |
| `not-detected` | servidor → cliente | (vacío) | Stream cerrado sin match. |

## Por qué `detect` es crítico

Wyoming **solo instancia los detectores para los nombres pedidos** en el
evento `detect`. Si el cliente abre conexión y manda directamente
`audio-start` sin `detect`, el servidor procesa audio pero `self.detectors`
queda vacío y **nunca emite `detection`**, aunque la wake word se diga
perfectamente. Es un bug que se manifiesta como "el cliente conecta, audio
llega, pero no detecta".

Fix aplicado en `38e9f21` (ver `client/backends/oww_client.py:connect`):
el método `connect()` ahora envía `detect` y `audio-start` antes de
devolver.

## Lógica interna de Wyoming 1.10.0 (resumen)

Para entender los flags `--threshold` y `--trigger-level`:

1. Por cada chunk de audio, Wyoming calcula un score `probability` (0.0-1.0).
2. Si `probability >= client_data.threshold`:
   - `activations += 1`.
   - Si `activations >= client_data.trigger_level`:
     - Emite `detection`, resetea `activations` a 0.
3. Si `probability < threshold`:
   - `activations = max(0, activations - 1)` (decae, no se resetea
     abruptamente).

`trigger_level=1` (default) significa que **basta un solo chunk por encima
del threshold** para disparar. Casi nunca hay que tocarlo. Si se quiere
más robustez ante falsos positivos, subir a 2-3.

## Ejemplo de flujo completo

Cliente (jota-voice) → Wyoming OWW:

```jsonl
{"type": "detect", "data": {"names": ["hey_jarvis"]}}
{"type": "audio-start", "data": {"rate": 16000, "width": 2, "channels": 1}, "data_length": 0}
```

Cliente envía chunks PCM (~512 samples = 32ms a 16 kHz):

```jsonl
{"type": "audio-chunk", "data": {"rate": 16000, "width": 2, "channels": 1, "timestamp": 0}, "payload_length": 1024}
<1024 bytes PCM>
```

Cuando el score supera el threshold:

```
Servidor → cliente:
{"type": "detection", "data": {"name": "hey_jarvis_v0.1", "timestamp": 1234}}
```

Si el cliente cierra el stream sin detección:

```
Servidor → cliente:
{"type": "not-detected"}
```

Esto último es lo que veíamos en los logs como `Audio stopped without
detection from client: <id>`.
