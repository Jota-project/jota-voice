---
name: wyoming-openwakeword
description: >
  Activar cuando el usuario mencione: Wyoming OpenWakeWord, wyoming-openwakeword, openWakeWord,
  rhasspy/wyoming-openwakeword, dscripka/openWakeWord, wake word, modelo .tflite, threshold OWW,
  "no detecta la wake word", bug upstream rhasspy/wyoming-openwakeword#53, imagen 2.1.0 / 1.10.0,
  instalación OWW en Mac, OWW en Termux, sles-source OWW, audio Android + wake word,
  jota-voice + wake word, "Triggered" o "Detection" en logs, modelo ok_jota, hey_jarvis.
  NO activar para: STT, TTS, voice pipeline general, Home Assistant (sin wake word),
  jota-display, jota-gateway (salvo que pregunten por la conexión cliente→OWW→gateway).
---

# Wyoming OpenWakeWord — Skill de jota-voice

## TL;DR

Wyoming OpenWakeWord (OWW) es el servidor de wake word que usa jota-voice. Detecta
una palabra/frase de activación (por defecto `hey_jarvis`) sobre un stream de audio
PCM a 16 kHz y emite un evento Wyoming cuando hay match. Corre en Docker (macOS) o
como venv Python (Termux/Android). En jota-voice es el **primer paso del pipeline
de voz**: si no detecta, el resto no se ejecuta.

## Conceptos clave

- **openWakeWord** (`dscripka/openWakeWord`): la librería Python/TFLite de detección
  de wake word. Es el motor.
- **Wyoming** (`rhasspy/wyoming`): el protocolo JSON-lines sobre TCP, peer-to-peer.
  Define los eventos `detect`, `audio-start`, `audio-chunk`, `detection`,
  `not-detected`, etc. Ver `references/protocol-wyoming.md`.
- **wyoming-openwakeword** (`rhasspy/wyoming-openwakeword`): el servidor que combina
  la librería openWakeWord con el protocolo Wyoming. Es lo que corre en el puerto
  10401 por defecto. Es lo que realmente desplegamos.
- **Threshold** (`--threshold`): score mínimo (0.0-1.0) para considerar que un chunk
  tiene la wake word. Por defecto 0.5 en Wyoming, **pero OpenWakeWord recomienda
  0.1-0.2 con audio de micro real**. Ver `references/docker-macos.md`.
- **Trigger level** (`--trigger-level`): número de activaciones consecutivas por
  encima del threshold antes de emitir `detection`. Default 1. Casi nunca hay que
  tocarlo.
- **Debug probability** (`--debug-probability`): flag que vuelca el score por
  chunk en los logs. Esencial para diagnosticar "no detecta". No está documentado
  en el README upstream; verificado en el código fuente de la imagen 1.10.0.
- **Custom model dir** (`--custom-model-dir`): directorio donde Wyoming busca
  modelos `.tflite` adicionales. Importante para el modelo `ok_jota` (ver
  `references/custom-model-training.md`).

## Cuándo usarlo / cuándo NO

**SÍ activar para:**
- Diagnosticar "la wake word no detecta".
- Decidir entre Docker y Termux para un dispositivo nuevo.
- Configurar threshold o debug-probability.
- Decidir si re-entrenar un modelo custom.
- Diagnosticar NXDOMAIN o errores de conexión cliente→OWW→gateway (issues
  del tipo "no se oye la respuesta del agente").

**NO activar para:**
- STT (transcripción), TTS (síntesis de voz), voice pipeline general → esas
  son otras skills, no esta.
- Home Assistant como integrador (HA es un consumidor de Wyoming, no el
  foco de jota-voice).
- jota-display, jota-gateway (salvo conexión cliente→OWW→gateway).
- Audio Android en general (sles-source de audio general ya está en la
  memoria del proyecto).

## Top 5 bugs que nos han costado tiempo

Resumen, ver `references/known-bugs.md` para detalle:

1. **Imagen 2.1.0 rota** ([rhasspy/wyoming-openwakeword#53](https://github.com/rhasspy/wyoming-openwakeword/issues/53)).
   Migró a `pyopen_wakeword` 1.1.0 incompatible con modelos `.tflite` existentes.
   `IndexError: tuple index out of range` silencioso. **Workaround: pinear
   `rhasspy/wyoming-openwakeword:1.10.0`**.
2. **Cliente sin evento `detect`** (fix `38e9f21`). Wyoming solo instancia
   detectores para los nombres pedidos en el evento `detect`. Sin él, no hay
   detección aunque el audio llegue.
3. **Threshold 0.3 alto**. OpenWakeWord recomienda 0.1-0.2 con audio de micro
   real. Con 0.3 se pierden muchas detecciones legítimas.
4. **Bug regex `_NAME_VERSION`** en `wyoming_openwakeword/__main__.py`:
   trunca `hey_jarvis_v0.1` a `hey`. Workaround: renombrar el fichero sin
   el sufijo `_v<n>`.
5. **Modelos custom sub-entrenados** (`ok_jota` 207 KB vs `hey_jarvis` 1.2 MB).
   El modelo no aprendió la wake word; no es bug del runtime, es del
   entrenamiento.

## Cómo diagnosticar "no detecta"

Procedimiento paso a paso completo en `references/troubleshooting.md`.
Resumen ejecutivo:

1. **¿Contenedor Wyoming corriendo?** `docker ps | grep wyoming-oww`.
2. **¿Carga el modelo?** `docker logs wyoming-oww | grep Loading`.
3. **¿Llegan probabilidades?** Relanzar con `--debug --debug-probability`,
   decir la wake word, buscar `probability=` en logs.
4. **¿Hay `Triggered`?** Buscar `Triggered <modelo>` (es la detección real).
5. **¿Scores demasiado bajos?** Si todos < 0.05 con voz clara → modelo
   incompatible. Probar otro bundled o re-entrenar.

## Modelo custom: cuándo re-entrenar

**NO re-entrenar** si:
- Hablas inglés americano y el modelo pre-entrenado (`hey_jarvis`) detecta
  con scores > 0.2 a la primera.
- El threshold es el problema, no el modelo (bájalo primero).

**SÍ re-entrenar** si:
- Hablas español u otro idioma distinto al inglés americano (los modelos
  pre-entrenados están optimizados para inglés).
- Quieres una frase propia (e.g. "oye jota") que no es wake word simple.
- Los scores con tu voz nunca superan 0.05 con threshold 0.1.

Pipeline completo en `references/custom-model-training.md`.

## Diferencias Docker (Mac) vs venv (Termux)

| Aspecto | Docker (Mac) | venv (Termux) |
|---|---|---|
| Imagen/paquete | `rhasspy/wyoming-openwakeword:1.10.0` | `openwakeword==0.5.1` + `wyoming-openwakeword==1.3.0` |
| Sistema de audio | sounddevice/PortAudio (cliente) | parec/sles-source (cliente) |
| Arquitectura | `linux/amd64`, `linux/arm64` | `linux/arm/v7` (Huawei P8 Lite), `linux/arm64` |
| Por qué venv | No hay Docker nativo fiable en Android | venv + `system-site-packages` para tflite-runtime y scipy vía Termux pkg |
| Instalación | `install/macos/04-oww.sh` | `install/04-oww.sh` |
| Detalles | `references/docker-macos.md` | `references/termux-installation.md` |

**Punto crítico de arquitectura**: la imagen Wyoming 1.10.0 es la **última con
soporte para `linux/arm/v7`**. Las versiones 2.x solo soportan `linux/amd64` y
`linux/arm64`. Si tu dispositivo es ARMv7 (Huawei P8 Lite, Raspberry Pi vieja)
**estás obligado a usar 1.10.0** por compatibilidad, no solo por el bug #53.

## Fuentes oficiales

Lista completa con URLs verificadas en `references/official-sources.md`. Las
5 principales:

- [github.com/rhasspy/wyoming-openwakeword](https://github.com/rhasspy/wyoming-openwakeword) (Apache-2.0, repo del servidor)
- [github.com/dscripka/openWakeWord](https://github.com/dscripka/openWakeWord) (Apache-2.0 + CC BY-NC-SA 4.0 modelos, repo de la librería)
- [github.com/rhasspy/wyoming](https://github.com/rhasspy/wyoming) (Apache-2.0, repo del protocolo)
- [hub.docker.com/r/rhasspy/wyoming-openwakeword/tags](https://hub.docker.com/r/rhasspy/wyoming-openwakeword/tags) (tags Docker, arquitecturas)
- [github.com/rhasspy/wyoming-openwakeword/issues/53](https://github.com/rhasspy/wyoming-openwakeword/issues/53) (issue upstream, ABIERTO)

Demo online sin instalar: [HuggingFace Spaces `davidscripka/openWakeWord`](https://huggingface.co/spaces/davidscripka/openWakeWord).

## Historia del proyecto jota-voice con OWW

| Commit/Issue | Qué pasó |
|---|---|
| Issue #4 (cerrada 2026-07-08) | Wake word no detecta con imagen 2.1.0 |
| `38e9f21` | `fix(oww): enviar evento "detect" antes de audio-start` |
| `61f0abd` | `fix(macos): pin wyoming-openwakeword:1.10.0 + threshold 0.15` |
| `f60e35c` | `docs(macos): nota sobre el workaround de wyoming-openwakeword 1.10.0` |
| Issue #5 (abierta 2026-07-08) | NXDOMAIN del dominio del gateway configurado en `devices/<id>/config.yaml` — typo en el dominio del despliegue del usuario. **No contiene URLs privadas**: el valor exacto vive en `devices/<id>/config.yaml` (gitignored). Causa raíz: un caracter de más/menos en el TLD del dominio. La cadena wake word → RECORDING → gateway se cortaba en el último tramo porque el resolver DNS abortaba antes de llegar al Tunnel. (Independiente de OWW.) |
| Memoria `feedback_audio_android.md` | 7 bugs del pipeline PulseAudio + sles-source (relevante para Termux, ver `references/termux-installation.md`) |
