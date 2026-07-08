# Diagnóstico paso a paso: "la wake word no detecta"

Sigue estos pasos en orden. **No te saltes ninguno**: el resultado de un paso
determina qué mirar en el siguiente.

## Paso 1: ¿El contenedor Wyoming está corriendo?

**macOS (Docker):**
```bash
docker ps | grep wyoming-oww
```

Salida esperada: línea con `wyoming-oww` y estado `Up X minutes`.

Si no sale:
```bash
sh install/macos/04-oww.sh
```

**Termux (venv):**
```bash
pgrep -f wyoming-openwakeword
```

Si vacío, arrancar manualmente o vía supervisord:
```bash
~/oww-venv/bin/python -m wyoming_openwakeword \
    --uri tcp://0.0.0.0:10401 \
    --custom-model-dir ~/oww-data \
    --threshold 0.15
```

## Paso 2: ¿El contenedor carga el modelo?

```bash
docker logs wyoming-oww 2>&1 | grep -E "Loading|Ready"
```

Salida esperada:
```
DEBUG:root:Loading /usr/src/.venv/lib/python3.11/site-packages/wyoming_openwakeword/models/melspectrogram.tflite
DEBUG:root:Loading /usr/src/.venv/lib/python3.11/site-packages/wyoming_openwakeword/models/embedding_model.tflite
INFO:root:Ready
```

Y, después de que el cliente conecte:
```
DEBUG:wyoming_openwakeword.handler:Started thread for hey_jarvis_v0.1
```

**Si no ves `Started thread for <modelo>`**: el cliente no envió `detect`
correctamente. Ver bug #3 en `known-bugs.md`.

**Si ves `IndexError: tuple index out of range`**: bug de la imagen 2.1.0.
Solución: pinear 1.10.0.

## Paso 3: ¿Llegan probabilidades al servidor?

Relanzar con debug:
```bash
docker stop wyoming-oww && docker rm wyoming-oww
cd "$HOME/wyoming-data"
docker run -d --name wyoming-oww --restart unless-stopped \
    -p 10401:10401 -v "$PWD:/data" \
    rhasspy/wyoming-openwakeword:1.10.0 \
    --uri tcp://0.0.0.0:10401 --custom-model-dir /data \
    --threshold 0.15 --debug --debug-probability
```

Decir la wake word varias veces (3-5 veces con 2-3 segundos entre cada una).
Después:
```bash
docker logs wyoming-oww | grep "probability="
```

**Salida esperada**: líneas como
```
DEBUG:root:client=<id>, wake_word=hey_jarvis_v0.1, probability=0.0123
DEBUG:root:client=<id>, wake_word=hey_jarvis_v0.1, probability=0.4521
```

**Si no hay ninguna línea de probabilidad**: el audio no llega. Verificar
el cliente con el script de diagnóstico RMS o con `parec`/sounddevice.

**Si las probabilidades se quedan siempre < 0.05 con voz clara**: el
modelo es incompatible con tu voz/idioma. Probar otro modelo bundled o
re-entrenar.

## Paso 4: ¿Hay detecciones reales?

```bash
docker logs wyoming-oww | grep "Triggered"
```

**Salida esperada (cuando funciona)**:
```
DEBUG:root:Triggered hey_jarvis_v0.1 (client=<id>)
```

Esta línea SOLO aparece cuando un chunk supera el threshold con
`trigger_level` activaciones. Es la **prueba inequívoca** de detección.

**Si no hay `Triggered` pero hay `probability=`**: el score nunca llegó
al threshold. Volver al paso 3 y bajar threshold a 0.10.

**Si hay `Triggered` pero el cliente no entra en RECORDING**: problema en
el cliente, no en Wyoming. Ver logs del cliente (`/tmp/voice_client.log`).

## Paso 5: ¿El threshold es correcto?

| Score máximo observado con voz clara | Threshold recomendado |
|---|---|
| < 0.05 | Modelo incompatible. Probar otro bundled o re-entrenar. |
| 0.05 - 0.15 | Bajar threshold a 0.10. |
| 0.15 - 0.30 | Threshold 0.15 funciona. |
| 0.30 - 0.50 | Threshold 0.25 o 0.30 funciona bien. |
| > 0.50 | Threshold 0.40 o 0.50 (más restrictivo, anti-falsos-positivos). |

## Paso 6: ¿El cliente publica el evento en el bus?

Si Wyoming emite `Triggered` y el cliente entra en RECORDING, el siguiente
log debería aparecer:
```
INFO backends.oww_client: Wake word detectado: hey_jarvis_v0.1
INFO backends.oww_client: OWW run_forever: detectado → 'hey_jarvis', invocando callback
INFO domain.state_machine: IDLE: wake word recibido → 'hey_jarvis'
```

Si todo esto aparece y el siguiente paso falla con `Errno 8` o similar, el
problema está en la conexión al gateway (issue #5), no en OWW.

## Resumen de señales de cada bug

| Bug | Señal típica |
|---|---|
| Imagen 2.1.0 rota | `IndexError: tuple index out of range` en logs Wyoming. O ninguna probabilidad logueada. |
| Regex `_NAME_VERSION` | Wyoming dice "Found custom model hey at ..." (no `hey_jarvis`). |
| Cliente sin `detect` | Wyoming carga el modelo base pero no `Started thread for <modelo>`. |
| Threshold alto | `Triggered` ausente, `probability=` cerca del threshold pero no superándolo. |
| Modelo sub-entrenado | `probability=` < 0.05 con voz clara. |
| NXDOMAIN gateway (no OWW) | `Triggered` aparece, cliente entra en RECORDING, falla con `Errno 8`. |
