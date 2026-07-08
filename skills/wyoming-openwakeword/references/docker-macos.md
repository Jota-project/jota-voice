# Wyoming OWW en Docker (macOS)

## Por qué pineamos `1.10.0`

Dos razones:

1. **Bug upstream en 2.1.0** ([rhasspy/wyoming-openwakeword#53](https://github.com/rhasspy/wyoming-openwakeword/issues/53)):
   la imagen 2.1.0 migró de `openwakeword` (Python + TFLite) a `pyopen_wakeword` 1.1.0
   (wrapper CFFI nativo). Los modelos `.tflite` distribuidos no son compatibles.
   El servidor arranca, carga modelos, recibe audio y **falla silenciosamente**
   sin emitir detecciones. El issue está ABIERTO, sin PRs.

2. **Compatibilidad de arquitectura**: la imagen `1.10.0` es la **última con
   soporte para `linux/arm/v7`**. Las 2.x solo soportan `linux/amd64` y
   `linux/arm64`. Si despliegas en Raspberry Pi vieja o dispositivo ARMv7,
   estás obligado a usar 1.10.0.

El pineo está en `install/macos/04-oww.sh` con un comentario inline que apunta
al issue, así que cualquier instalación nueva del repo queda protegida.

## Flags de `docker run` que usamos

| Flag | Valor | Por qué |
|---|---|---|
| `--name` | `wyoming-oww` | Nombre estable para `docker start/stop/logs`. |
| `--restart` | `unless-stopped` | Reinicia automáticamente tras reboot. |
| `-p` | `10401:10401` | Puerto TCP estándar de Wyoming OWW. |
| `-v` | `${OWW_DATA_DIR}:/data` | Volumen para modelos custom. `OWW_DATA_DIR` viene de `install/macos/00-lib.sh`, default `$HOME/wyoming-data`. |
| `--uri` | `tcp://0.0.0.0:10401` | Wyoming escucha en todas las interfaces, no solo loopback (necesario si el cliente viene de otro dispositivo). |
| `--custom-model-dir` | `/data` | Ruta DENTRO del contenedor donde está montado el volumen. Wyoming busca `*.tflite` recursivamente. |
| `--threshold` | `0.15` (env `OWW_THRESHOLD`, default 0.15) | OpenWakeWord recomienda 0.1-0.2 con audio de micro real. El 0.5 por defecto de Wyoming es demasiado alto. |
| `--debug` | (opcional, para diagnosticar) | Sube el nivel de log a DEBUG. Implica `--debug-probability` (volca `probability=` por chunk). |

`--debug-probability` es la flag que **siempre** hay que activar cuando algo
no funciona: dice qué score está dando el modelo por cada chunk de audio.

## Volumen de datos: dónde van los modelos

`$HOME/wyoming-data/` (default; configurable vía `OWW_DATA_DIR`).

Estructura típica:
```
wyoming-data/
├── hey_jarvis.tflite            # pre-entrenado oficial (renombrado sin _v0.1 por bug regex)
├── hey_jarvis_bundled.tflite    # copia del bundled de la imagen, por si se borra
├── okay_nabu_bundled.tflite     # otro modelo bundled
└── ok_jota.tflite               # custom del usuario (sub-entrenado, 207 KB)
```

**No renombrar `hey_jarvis_v0.1.tflite`** en este directorio: la imagen 1.10.0
sufre el bug regex `_NAME_VERSION` que trunca a `hey`. Workaround documentado
en `known-bugs.md` y aplicado en `devices/macbook_sito/`: el fichero está
renombrado a `hey_jarvis.tflite`.

## Verificación tras levantar el contenedor

Comando:
```bash
docker logs wyoming-oww --tail 20
```

Salida esperada (1.10.0, no 2.1.0):
```
DEBUG:root:Namespace(uri='tcp://0.0.0.0:10401', ..., threshold=0.15, debug=True, debug_probability=True, ...)
DEBUG:root:Loading /usr/src/.venv/lib/python3.11/site-packages/wyoming_openwakeword/models/melspectrogram.tflite
DEBUG:root:Loading /usr/src/.venv/lib/python3.11/site-packages/wyoming_openwakeword/models/embedding_model.tflite
INFO:root:Ready
```

Líneas clave:
- `Loading melspectrogram.tflite` y `Loading embedding_model.tflite`: modelo base cargado.
- `INFO:root:Ready`: servidor aceptando conexiones en 10401.
- `Loading hey_jarvis_v0.1 from ...`: el modelo de wake word concreto se carga
  cuando un cliente pide `detect` con `names=['hey_jarvis']`.
- `Triggered hey_jarvis_v0.1 (client=<id>)`: **detección real**. Solo aparece
  cuando un chunk supera el threshold con `trigger_level` activaciones.

Líneas de error a buscar:
- `IndexError: tuple index out of range` → bug 2.1.0. Solución: pinear 1.10.0.
- `Audio stopped without detection from client: <id>` → stream cerrado sin
  detección. Puede ser legítimo (no dijiste la wake word) o síntoma de
  problema.

## Troubleshooting específico de Mac

- **Permisos de micrófono (TCC)**: macOS pide permiso la primera vez. Si el
  cliente no captura audio, abrir `Configuración del Sistema > Privacidad y
  seguridad > Micrófono` y dar permiso a la app que lanza Python (Terminal,
  iTerm, VS Code...).
- **PortAudio y CoreAudio**: `sounddevice` se basa en PortAudio, que en macOS
  usa CoreAudio. No siempre aparece en el panel de "aplicaciones que usan el
  micro" del sistema, especialmente si el proceso es un subproceso de Python
  sin bundle propio. **No es bug**: el indicador del sistema es parcial.
- **Puerto 8765 in use**: warning habitual al arrancar el cliente jota-voice,
  es el `ControlServer` que no puede bindear. No es bloqueante.

## Snippet de `install/macos/04-oww.sh`

El script es el source of truth. El pin, el threshold y el log de versión
están todos ahí. Ver también el `commit 61f0abd` que documenta el cambio
en el mensaje.
