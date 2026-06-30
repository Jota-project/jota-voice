# Señal cancel en jota-voice

**Fecha:** 2026-06-28
**Scope:** cliente jota-voice (no gateway, no jota-display)

## Contexto

El gateway ya soporta `{"type":"cancel"}` para abortar el turn activo. El cliente no tiene mecanismo para enviarlo. La señal se puede disparar desde:

- Un botón en jota-display (interfaz principal)
- Un botón físico/táctil en dispositivo headless (futuro)
- Un nuevo wake word (el gateway ya cancela el turn anterior al recibir nueva conexión — sin trabajo extra)

## Diseño

### Componentes

**`gateway_client.py`**
Añadir `send_cancel()`: envía `{"type":"cancel"}` al gateway. Mismo patrón que `send_end()`.

**`control_server.py`** (fichero nuevo)
Servidor HTTP asyncio mínimo. Corre como task adicional en el event loop de `voice_client.py`. Sin dependencias externas (stdlib asyncio).

Endpoint único:
- `POST /cancel` → `cancel_event.set()` → responde `200 OK`

Puerto configurable vía `config.yaml` (`control.port`, default `8765`).

**`state_machine.py`**
- `_recording()` y `_responding()` reciben `cancel_event: asyncio.Event` como parámetro nuevo.
- Al inicio de `_recording()`: `cancel_event.clear()` para descartar cancels de turns anteriores.
- En ambos estados: `asyncio.wait([tarea_principal, cancel_event.wait()], return_when=FIRST_COMPLETED)`.
- Si el cancel_event gana: cancela la tarea principal, llama a `gateway.send_cancel()`, llama a `playback.reset()`, lanza `_TurnCancelled`.
- `_TurnCancelled`: excepción privada nueva. El `run()` la captura en RECORDING y RESPONDING, hace cleanup y vuelve a IDLE (mismo flujo que otras excepciones de estado).
- Si cancel ocurre en RECORDING: `run()` salta RESPONDING directamente.

**`config.py` + `config.example.yaml`**
Nuevo bloque:
```yaml
control:
  port: 8765
```

**`voice_client.py`**
- Crea `cancel_event = asyncio.Event()`.
- Arranca `control_server` como task asyncio antes del loop principal.
- Pasa `cancel_event` a `state_machine.run()`.

### Flujo de datos

```
jota-display (botón cancel)
  → POST http://127.0.0.1:8765/cancel
  → control_server: cancel_event.set()

state_machine (RECORDING o RESPONDING):
  asyncio.wait([tarea_principal, cancel_event.wait()])
  → cancel_event gana
  → cancela tarea principal
  → gateway.send_cancel()  →  {"type":"cancel"}
  → playback.reset()
  → lanza _TurnCancelled
  → run(): cleanup → IDLE
```

### Casos límite

| Caso | Comportamiento |
|---|---|
| Control server no arranca (puerto ocupado) | Warning en log, proceso continúa sin cancel por botón |
| `send_cancel()` falla (gateway desconectado) | Error descartado silenciosamente — cleanup continúa |
| Cancel llega en IDLE | `cancel_event.set()` queda pendiente; `clear()` al inicio de RECORDING lo descarta |
| Cancel duplicado (dos POSTs) | Segundo `set()` es no-op |

### Contrato con jota-display

- Endpoint: `POST http://127.0.0.1:{control.port}/cancel`
- Respuesta: `200 OK`, body vacío
- Sin autenticación (localhost only)
- jota-display necesita conocer el puerto (fijo 8765 por defecto, o leerlo de config)

## Ficheros modificados

| Fichero | Cambio |
|---|---|
| `client/gateway_client.py` | Añadir `send_cancel()` |
| `client/control_server.py` | Nuevo — servidor HTTP de control |
| `client/state_machine.py` | `_TurnCancelled`, cancel_event en `_recording` y `_responding` |
| `client/config.py` | `ControlConfig` con `port: int = 8765` |
| `client/voice_client.py` | Crear cancel_event, arrancar control_server, pasar a run() |
| `config.example.yaml` | Añadir bloque `control:` |
