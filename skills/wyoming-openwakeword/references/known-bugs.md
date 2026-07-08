# Bugs conocidos de Wyoming OWW y openWakeWord

## 1. Imagen 2.1.0 rota (issue #53)

**Síntoma**: Wyoming arranca, carga el modelo, recibe audio del cliente, y
**nunca emite detecciones**. El servidor no loguea errores. El cliente ve
conexiones exitosas.

**Causa**: la imagen 2.1.0 migró de `openwakeword` (Python + TFLite) a
`pyopen_wakeword` 1.1.0 (wrapper CFFI nativo). Los modelos `.tflite`
distribuidos no son compatibles. La llamada `OpenWakeWord.process_streaming`
lanza `IndexError: tuple index out of range` en cada chunk, silenciosamente.

**Workaround**: pinear `rhasspy/wyoming-openwakeword:1.10.0`. Está aplicado
en `install/macos/04-oww.sh` (commit `61f0abd`).

**Issue upstream**: [rhasspy/wyoming-openwakeword#53](https://github.com/rhasspy/wyoming-openwakeword/issues/53)
(ABIERTO, sin PR).

**Verificación adicional**: la imagen 1.10.0 es la **última con soporte
para `linux/arm/v7`**. Para dispositivos ARMv7, **no hay vuelta atrás a
2.x** por compatibilidad de arquitectura, no solo por este bug.

## 2. Bug regex `_NAME_VERSION` en Wyoming 1.10.0

**Síntoma**: el cliente pide `detect` con `names=["hey_jarvis"]`, el modelo
está en `--custom-model-dir` como `hey_jarvis_v0.1.tflite`, y Wyoming dice
"modelo no encontrado". En el log aparece `Found custom model hey at ...`
(no `hey_jarvis`).

**Causa**: la regex `^([^_]+)_v[0-9.]+$` en
`wyoming_openwakeword/__main__.py` trunca `hey_jarvis_v0.1` a `hey`
(captura solo el primer segmento antes del `_`).

**Workaround**: renombrar el fichero sin el sufijo `_v<n>`:
```bash
mv ~/wyoming-data/hey_jarvis_v0.1.tflite ~/wyoming-data/hey_jarvis.tflite
```

Esto se aplicó en `devices/macbook_sito/`.

## 3. Cliente sin evento `detect`

**Síntoma**: el cliente Wyoming conecta, envía audio, Wyoming lo procesa,
pero **nunca emite detección** aunque la wake word se diga. El log de
Wyoming muestra `Client connected: <id>` y `Receiving audio from
client: <id>` pero no `Triggered`.

**Causa**: Wyoming solo instancia detectores para los nombres pedidos en
el evento `detect`. Si el cliente abre conexión y envía directamente
`audio-start`, los detectores no se crean.

**Workaround**: el cliente debe enviar `detect` antes de `audio-start`.
Implementado en `client/backends/oww_client.py:connect` (commit `38e9f21`).

**Verificación**: en los logs de Wyoming, la presencia de
`Loading hey_jarvis_v0.1 from ...` indica que el `detect` se procesó
correctamente. Si no aparece, el cliente no está enviando el evento.

## 4. Threshold 0.3 (default) demasiado alto

**Síntoma**: la wake word se dice claramente pero no se detecta. Los
`probability=` en `--debug-probability` muestran valores 0.10-0.30.

**Causa**: OpenWakeWord recomienda 0.1-0.2 con audio de micro real. El
default de Wyoming (0.5 en el código, configurable) es para speech limpio
de podcast; con audio ambiental y voz no nativa los scores se quedan en
0.1-0.2.

**Workaround**: bajar threshold a 0.15 (o 0.10 si 0.15 sigue siendo alto).
Configurable por env `OWW_THRESHOLD=0.10 sh install/macos/04-oww.sh`.

**Riesgo**: threshold muy bajo (< 0.05) genera falsos positivos (detección
sin wake word dicha, e.g. con ruido de TV).

## 5. Modelos custom sub-entrenados (caso `ok_jota`)

**Síntoma**: scores < 0.05 con voz clara, no se detecta nunca.

**Causa**: el modelo `.tflite` no aprendió la wake word. Típicamente:
- Pocas muestras positivas (< 500).
- Pocas iteraciones de entrenamiento.
- Arquitectura de red pequeña.

`ok_jota` actual: 207 KB. Modelos bien entrenados pesan **1-4 MB** (e.g.
`hey_jarvis` = 1.2 MB). El ratio 207 KB vs 1.2 MB es señal clara de
sub-entrenamiento.

**Workaround**: re-entrenar con `notebooks/automatic_model_training.ipynb`
del repo openWakeWord. Ver `custom-model-training.md`.

## 6. NXDOMAIN del gateway (issue #5, NO es bug de OWW)

**Síntoma**: la wake word se detecta correctamente (logs Wyoming muestran
`Triggered`), el cliente entra en `RECORDING`, pero falla con
`[Errno 8] nodename nor servname provided, or not known`.

**Causa**: el cliente intenta conectar al gateway
(`green-house.alfonsogare.com` o similar) y el DNS no resuelve. Es
problema de infraestructura/red, **no de Wyoming OWW**.

**Workaround**: cambiar `gateway.url` en `devices/<id>/config.yaml` a la
IP local del gateway mientras se diagnostica el DNS.

**Issue**: [jota-voice#5](https://github.com/Jota-project/jota-voice/issues/5).
