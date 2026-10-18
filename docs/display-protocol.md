# Protocolo display — jota-voice

Contrato entre **jota-voice** (servidor) y cualquier **display** (cliente) que
quiera mostrar lo que hace el asistente de voz: el kiosk web de jota-display,
el HUD de macOS o cualquier otro. Este documento es la fuente de verdad: la
implementación de referencia es `client/backends/display_ws.py`
(`WsDisplayBackend`) y la traducción de eventos internos está en
`client/app/display_client.py`.

**Versión del protocolo: 1**

## Índice

1. [Visión general](#1-visión-general)
2. [Versión del protocolo](#2-versión-del-protocolo)
3. [Conexión y autenticación](#3-conexión-y-autenticación)
4. [Envelope](#4-envelope)
5. [Mensajes servidor → display](#5-mensajes-servidor--display)
6. [Mensajes display → servidor](#6-mensajes-display--servidor)
7. [Orden, reconexión e idempotencia](#7-orden-reconexión-e-idempotencia)
8. [Varios displays a la vez](#8-varios-displays-a-la-vez)
9. [Displays lentos](#9-displays-lentos)
10. [Ejemplo: un turno completo](#10-ejemplo-un-turno-completo)
11. [Compatibilidad: backend http (kiosk actual)](#11-compatibilidad-backend-http-kiosk-actual)
12. [Evolución del protocolo](#12-evolución-del-protocolo)
13. [Referencia rápida](#13-referencia-rápida)

---

## 1. Visión general

- jota-voice abre un **servidor WebSocket** en `ws://127.0.0.1:8767` (backend
  `ws`, por defecto en macOS y Linux). Solo escucha en loopback: los displays
  corren en la misma máquina.
- El display se conecta, se autentica con un token y recibe:
  1. un `sync` con el estado completo actual;
  2. a partir de ahí, un mensaje por cada cambio (push).
- El display puede mandar comandos por la misma conexión: hoy `cancel`
  (cancelar el turno activo) y `resync`.
- Todos los mensajes son **frames de texto con un objeto JSON** (UTF-8). No
  hay frames binarios.
- Un display nunca necesita histórico: con el `sync` y los mensajes
  siguientes siempre puede pintar lo correcto.

Puerto: `8767` (configurable con `display.port`). No es el `8766`, que usa el
servidor del kiosk de jota-display en el mismo teléfono, ni el `8765`, que era
el antiguo ControlServer (`POST /cancel`), eliminado.

## 2. Versión del protocolo

El `sync` lleva `protocol_version` (entero). Un display debe comprobarlo
antes de interpretar ningún otro mensaje:

- Misma versión: adelante.
- Versión mayor que la que conoce: mostrar un aviso ("actualiza el display")
  y no asumir el formato de los mensajes.

Los cambios **aditivos** (campos nuevos, tipos de mensaje nuevos, valores
nuevos de `health.state`) no cambian la versión: un display debe ignorar los
campos y tipos que no conozca. Cualquier cambio incompatible (quitar o
renombrar un campo, cambiar su significado) sube la versión.

## 3. Conexión y autenticación

### Token

- Fichero: `~/.jota-voice/display_token` (configurable con
  `display.token_path`). Lo crea jota-voice al arrancar: 64 caracteres hex,
  permisos `0600`, del mismo usuario que ejecuta jota-voice. En cada arranque
  se re-verifican los permisos y se corrigen a `0600` si están más abiertos.
- El fichero se publica de forma atómica: nunca existe vacío. Si un display
  lo lee vacío (solo posible en sistemas de ficheros sin hard links, durante
  la creación), debe reintentar al cabo de unos milisegundos.
- Un display lee el token del fichero en cada intento de conexión (jota-voice
  lo regenera si el fichero se borra o se vacía).

### Handshake

```
GET / HTTP/1.1
Host: 127.0.0.1:8767
Upgrade: websocket
Connection: Upgrade
X-Jota-Display-Token: <token>
...
```

| Respuesta | Significado | Qué hacer |
|---|---|---|
| `101` | Conectado | Esperar el `sync` |
| `401` | Token ausente o incorrecto | Releer el fichero de token y reintentar con backoff |
| `429` | Demasiados intentos con token incorrecto en la ventana (`display.rate_limit_*`, por defecto 10 en 10 s) | Esperar y reintentar |
| `503` | Ya hay `display.max_clients` displays conectados (por defecto 8) | Reintentar con backoff |
| conexión rechazada | jota-voice no está corriendo o el puerto está ocupado | Reintentar con backoff |

El rate limit **solo cuenta los intentos con token incorrecto**: un display
con el token válido nunca recibe `429`, aunque algo esté probando tokens al
mismo tiempo.

### Por qué una cabecera

La API `WebSocket` de un navegador **no puede fijar cabeceras**. Así, una
página web cualquiera abierta en la máquina no puede conectarse a este
servidor (ni por cross-site WebSocket hijacking). La contrapartida es que un
frontend web tampoco puede conectarse directamente: un display web necesita
un proceso nativo que haga de puente (ver §11 y el plan de migración del
kiosk). Las apps nativas (HUD de macOS con `URLSessionWebSocketTask`, Python
con `websockets`, etc.) fijan la cabecera sin problema.

### Keepalive

El servidor manda pings de WebSocket cada 20 s y cierra la conexión si no
recibe el pong. Las librerías WebSocket responden a los pings solas; no hace
falta ningún mensaje de aplicación.

### Límites

- Mensajes display → servidor: máximo 4096 bytes. Uno mayor cierra la
  conexión (código 1009).
- JSON inválido o con forma inesperada: se ignora y la conexión sigue.

## 4. Envelope

Todos los mensajes servidor → display tienen esta forma:

```json
{
  "type": "state",
  "seq": 42,
  "ts": 1760781234.123,
  "turn_id": "t-7",
  "data": { "value": "listening", "text": "" }
}
```

| Campo | Tipo | Significado |
|---|---|---|
| `type` | string | Tipo de mensaje (§5) |
| `seq` | int | Contador del servidor, crece en 1 con cada mensaje emitido. En un `sync`, es el `seq` del último mensaje que el snapshot ya incluye |
| `ts` | float | Hora de emisión (epoch, segundos) |
| `turn_id` | string \| null | Turno activo al emitir (el del gateway), o `null` fuera de turno |
| `data` | object | Contenido según el tipo |

El `sync` añade además `protocol_version` en el nivel superior.

Los mensajes display → servidor son objetos JSON con al menos `type` (§6).

## 5. Mensajes servidor → display

### `sync` — snapshot completo

Siempre es el **primer mensaje** de cada conexión. También se manda como
respuesta a `resync` y cuando un display se queda atrás (§9).

```json
{
  "type": "sync", "seq": 41, "ts": 1760781234.1, "turn_id": "t-7",
  "protocol_version": 1,
  "data": {
    "state": "response",
    "text": "Mañana hará sol en",
    "turn_id": "t-7",
    "health": {
      "gateway.transcriber": {
        "key": "gateway.transcriber", "level": "info", "service": "transcriber",
        "state": "degraded", "ttl_s": 20.0, "detail": {}
      }
    }
  }
}
```

| Campo de `data` | Significado |
|---|---|
| `state` | Estado actual (ver `state`) |
| `text` | Texto asociado: la transcripción en `thinking`, el texto acumulado de la respuesta en `response`, `""` en el resto |
| `turn_id` | Turno activo o `null` |
| `health` | Avisos de salud activos, por `key` (ver `health`). `{}` si no hay ninguno |

Al recibir un `sync` el display **sustituye** todo su estado por el del
snapshot (no lo mezcla).

### `state` — fase del asistente

```json
{ "type": "state", "data": { "value": "thinking", "text": "qué tiempo hace mañana" } }
```

| `value` | Cuándo | `text` |
|---|---|---|
| `idle` | Esperando la wake word | `""` |
| `listening` | Grabando al usuario | `""` |
| `thinking` | Transcripción lista, esperando respuesta | La transcripción |
| `response` | Empieza a sonar la respuesta | `""` (el texto llega por `text_update`) |

`idle` cierra el turno: el servidor pone `turn_id` a `null` y vacía el texto.

### `turn_start` — empieza nuestro turno

```json
{ "type": "turn_start", "turn_id": "t-7", "data": { "turn_id": "t-7", "turn_seq": 7 } }
```

Llega cuando el gateway abre el turno que responde al usuario (después de
`thinking` y antes del primer `text_update`). Los mensajes siguientes llevan
ese `turn_id` en el envelope hasta el `idle`. Los turnos iniciados por el
agente (push) no se muestran: no generan `turn_start` ni texto.

### `text_update` — texto revelado por chunk de audio

```json
{
  "type": "text_update", "turn_id": "t-7",
  "data": {
    "chunk_text": " hará sol en",
    "cumulative_text": "Mañana hará sol en",
    "duration_s": 0.84
  }
}
```

Uno por cada chunk de audio TTS, justo cuando empieza a sonar:

| Campo | Significado |
|---|---|
| `chunk_text` | Texto nuevo que corresponde a este chunk (puede ser `""`) |
| `cumulative_text` | Todo el texto de la respuesta hasta ahora |
| `duration_s` | Duración del audio de este chunk |

El display anima el reveal de `chunk_text` durante `duration_s` (tipo
máquina de escribir) para que el texto vaya al ritmo de la voz. Si llega el
siguiente `text_update` antes de terminar, salta al final del anterior.
`cumulative_text` permite pintar lo correcto aunque se haya perdido algún
mensaje: un display que no anime puede mostrar directamente
`cumulative_text`.

### `tool_call` — herramienta del agente

```json
{
  "type": "tool_call", "turn_id": "t-7",
  "data": {
    "phase": "start", "name": "exec", "tool_call_id": "call-1",
    "args": { "command": "ls" }, "result": null, "is_error": null
  }
}
```

Reenvío de los `tool_call` del gateway (jota-gateway
`docs/client-protocol.md` §5; solo llegan si el cliente tiene
`tool_calls_enabled`). `phase` es `start` o `result`; `tool_call_id`
correlaciona ambos. En `result` vienen `result` e `is_error`.

### `error` — el turno terminó con error

```json
{ "type": "error", "data": { "code": "TURN_ERROR", "message": "orchestrator unavailable", "fatal": false } }
```

| Campo | Significado |
|---|---|
| `code` | Código del gateway (`TURN_ERROR`, `GATEWAY_DISCONNECTED`, `GATEWAY_HANDSHAKE_REJECTED`...) o `null` para errores locales |
| `message` | Texto legible |
| `fatal` | `true` si no se va a recuperar solo (p. ej. clave rechazada) |

Es un **evento, no un estado**: no cambia el snapshot, y normalmente le sigue
un `state: idle` casi inmediato. El display decide cuánto tiempo lo muestra
(recomendado: unos 2-3 s, salvo que llegue antes `listening`).

### `cancelled` — el turno se canceló

```json
{ "type": "cancelled", "data": { "state": "RESPONDING" } }
```

`state` es la fase en la que se canceló (`RECORDING` o `RESPONDING`): por
una nueva wake word, por un `cancel` de un display o por barge-in del
gateway. También es un evento; le sigue un `idle`.

### `health` — avisos de salud

Estado informativo de los servicios de los que depende la voz. **No son
errores**: el asistente puede seguir funcionando.

```json
{
  "type": "health",
  "data": {
    "key": "gateway.transcriber", "level": "info", "service": "transcriber",
    "state": "degraded", "ttl_s": 20.0, "detail": {}
  }
}
```

| Campo | Significado |
|---|---|
| `key` | Identificador del aviso. Uno nuevo con la misma `key` sustituye al anterior |
| `level` | `ok` (limpia el aviso), `info` (aviso leve) o `warning` (algo no funciona) |
| `service` | `gateway`, `orchestrator`, `transcriber`, `tts` u `oww` |
| `state` | Estado que lo originó (abajo) |
| `ttl_s` | Si no es `null`, el aviso caduca solo al cabo de esos segundos |
| `detail` | Campos extra del origen (`code`, `reason`, `attempt`...) |

Origen y significado:

| `key` | `state` | `level` |
|---|---|---|
| `gateway` (la sesión de jota-voice con el gateway) | `disconnected`, `reconnecting` | `warning` |
| `gateway` | `connected` | `ok` |
| `gateway.<servicio>` (estado de servicios reportado por el gateway, client-protocol §8) | `unavailable`, `reconnecting` | `warning` |
| `gateway.<servicio>` | `restored` | `ok` |
| `gateway.transcriber` | `degraded` (calidad: silencio, buffer lleno) | `info`, `ttl_s` 20 |
| `oww` (wake word) | `degraded` (N reconexiones fallidas seguidas) | `warning` |
| `oww` | `recovered` | `ok` |
| cualquiera | estado desconocido | `info`, `ttl_s` 20 |
| cualquiera | `expired` (lo emite el servidor al caducar un aviso con `ttl_s`) | `ok` |

El servidor gestiona la caducidad: cuando vence `ttl_s` quita el aviso del
snapshot y emite un `health` con `level: ok` y `state: expired`. Un display
no necesita temporizadores, pero si los usa no pasa nada.

Nota: hoy el gateway manda `transcriber degraded` falsos con la sesión en
reposo (jota-gateway#227). Por eso `degraded` es `info` y caduca solo: los
displays no deben mostrarlo como un error.

## 6. Mensajes display → servidor

### `cancel` — cancelar el turno activo

```json
{ "type": "cancel", "turn_id": "t-7" }
```

- `turn_id` es opcional. Si se manda y no coincide con el turno activo, el
  servidor lo ignora: un `cancel` que llega tarde (o que se reenvía al
  reconectar) nunca cancela un turno posterior.
- Sin `turn_id`, cancela lo que esté en curso (grabación o respuesta).
- Es idempotente: mandarlo dos veces o sin turno activo no tiene efecto
  extra. Si cancela algo, el display verá `cancelled` y después `idle`.

### `resync` — pedir un `sync`

```json
{ "type": "resync" }
```

El servidor responde con un `sync` a ese display. Útil si el display detecta
un hueco en `seq` o quiere repintar desde cero.

Cualquier otro `type` se ignora sin cerrar la conexión.

## 7. Orden, reconexión e idempotencia

- Los mensajes llegan a cada display **en orden de emisión**.
- `seq` crece en 1 con cada mensaje emitido. Si el servidor descarta
  mensajes de un display que se ha quedado atrás (§9), el salto de `seq`
  aparece **en un `sync`**: el snapshot ya incluye lo descartado y los
  mensajes que le siguen vuelven a ser consecutivos. Un salto en un mensaje
  que no sea `sync` no debería ocurrir; si un display lo ve, puede mandar
  `resync`.
- Mensajes con `seq` menor o igual que el del último `sync` recibido son
  anteriores al snapshot: se pueden descartar.
- **Reconectar siempre es seguro**: el primer mensaje es un `sync` con el
  estado real, así que el display converge sin histórico. Al perder la
  conexión: reintentar con backoff (p. ej. 0.5, 1, 2, 5 s, máximo 5 s),
  releyendo el token en cada intento.
- `seq` empieza en 0 cada vez que arranca jota-voice. Tras reconectar, el
  display toma el `seq` del `sync` como nueva referencia.

## 8. Varios displays a la vez

- El servidor admite hasta `display.max_clients` displays simultáneos (por
  defecto 8), p. ej. el kiosk y el HUD de macOS.
- Todos reciben los mismos mensajes con el mismo `seq`.
- Cualquiera puede mandar `cancel`; gracias a `turn_id` y a la idempotencia,
  que dos displays cancelen a la vez no tiene efectos dobles.
- Un display lento no afecta a los demás (§9).

## 9. Displays lentos

Cada display tiene su propia cola de salida (256 mensajes). Si un display no
lee lo bastante rápido y su cola se llena, el servidor **descarta lo
pendiente de ese display y le encola un `sync`** con el estado actual. Ni el
pipeline de voz ni los demás displays esperan nunca a un display lento.

## 10. Ejemplo: un turno completo

```
← sync         seq=0  {state: idle, text: "", turn_id: null, health: {}}
← state        seq=1  {value: listening}
← state        seq=2  {value: thinking, text: "qué tiempo hace mañana"}
← turn_start   seq=3  turn_id=t-7 {turn_id: t-7, turn_seq: 7}
← state        seq=4  turn_id=t-7 {value: response, text: ""}
← text_update  seq=5  turn_id=t-7 {chunk_text: "Mañana", cumulative_text: "Mañana", duration_s: 0.6}
← text_update  seq=6  turn_id=t-7 {chunk_text: " hará sol.", cumulative_text: "Mañana hará sol.", duration_s: 1.1}
← state        seq=7  {value: idle}
```

Con un `cancel` del display durante la respuesta:

```
→ cancel       {turn_id: t-7}
← cancelled    seq=8  turn_id=t-7 {state: RESPONDING}
← state        seq=9  {value: idle}
```

## 11. Compatibilidad: backend http (kiosk actual)

El kiosk de jota-display todavía no habla este protocolo: su servidor
(`jota-display/server/server.py`, puerto 8766) recibe `POST /state` con
`{"state", "text"}` y lo reenvía a su frontend por SSE. Mientras no migre,
jota-voice lo atiende con el backend `http` (por defecto en Termux y siempre
que haya `display.url`), que traduce este protocolo con pérdida:

| Mensaje del protocolo | POST /state |
|---|---|
| `state` (idle, listening, thinking, response) | `{"state": value, "text": text}` |
| `text_update` | `{"state": "response", "text": <texto revelado>}`, con un reveal local a pasos de 0,1 s durante `duration_s` |
| `turn_start`, `tool_call`, `error`, `cancelled`, `health` | no se envían (el kiosk guardaría un `state` que no entiende) |

El backend `http` no recibe comandos: con él, un `cancel` desde el kiosk no
llega a jota-voice (el botón de cancelar del kiosk actual solo resetea su
propia pantalla).

Para migrar el kiosk a `ws`, el servidor de jota-display hace de puente (un
navegador no puede fijar la cabecera del token, §3): lee el token, se conecta
a `ws://127.0.0.1:8767` con la cabecera, traduce los mensajes a su stream SSE
y reenvía como `{"type": "cancel"}` el cancel del frontend. Cuando eso esté
desplegado, la config del teléfono pasa a `display.backend: "ws"` y el backend
`http` se podrá retirar.

## 12. Evolución del protocolo

- Añadir un campo a `data`, un tipo de mensaje o un valor de `state`/`level`
  no sube `protocol_version`. Los displays ignoran lo que no conocen.
- Quitar o renombrar campos, o cambiar su significado, sube
  `protocol_version` y se documenta aquí.
- Cualquier cambio en `display_ws.py` o `display_client.py` que afecte a lo
  que ve un display se refleja primero en este documento.

## 13. Referencia rápida

### Servidor → display

| `type` | `data` | Snapshot |
|---|---|---|
| `sync` | `{state, text, turn_id, health}` + `protocol_version` | — |
| `state` | `{value: idle\|listening\|thinking\|response, text}` | Sí |
| `turn_start` | `{turn_id, turn_seq}` | Sí (`turn_id`) |
| `text_update` | `{chunk_text, cumulative_text, duration_s}` | Sí (`state: response`, `text`) |
| `tool_call` | `{phase, name, tool_call_id, args, result, is_error}` | No |
| `error` | `{code, message, fatal}` | No |
| `cancelled` | `{state}` | No |
| `health` | `{key, level: ok\|info\|warning, service, state, ttl_s, detail}` | Sí (`health`) |

### Display → servidor

| `type` | Campos | Efecto |
|---|---|---|
| `cancel` | `turn_id?` | Cancela el turno activo (si coincide) |
| `resync` | — | El servidor manda un `sync` |

### Configuración (`config.yaml`, sección `display`)

| Clave | Por defecto | Significado |
|---|---|---|
| `backend` | `null` (por plataforma: `ws` en macOS/Linux, `http` en Termux o con `url`) | `ws`, `http` o `null` |
| `port` | `8767` | Puerto del servidor ws (solo 127.0.0.1) |
| `token_path` | `~/.jota-voice/display_token` | Fichero del token |
| `rate_limit_max_requests` | `10` | Handshakes con token incorrecto por ventana |
| `rate_limit_window_s` | `10.0` | Ventana del rate limit |
| `max_clients` | `8` | Displays simultáneos |
| `url` | `""` (el backend http usa `http://127.0.0.1:8766`) | Solo backend http |
| `timeout_s` | `2.0` | Solo backend http |
