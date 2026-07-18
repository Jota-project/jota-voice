# Wyoming OWW en Termux (Android)

## Por qué venv y no Docker

Termux es un emulador de terminal Linux sobre Android. No hay Docker nativo
fiable (las opciones con `proot` tienen rendimiento pobre para inferencia
continua). La alternativa que usamos es un **venv Python con
`--system-site-packages`** que aprovecha los paquetes de Termux `pkg`.

## Paquetes pineados

| Paquete | Versión | Por qué |
|---|---|---|
| `openwakeword` | `0.5.1` | Última estable con wheels para Python 3.13 (la de Termux en P8 Lite). |
| `wyoming-openwakeword` | `1.3.0` | Adaptador Wyoming 1.x compatible con openWakeWord 0.5.1. |
| `wyoming` | `1.1.0` | Protocolo Wyoming. |
| `audioop-lts` | latest | Backport de `audioop` (eliminado en Python 3.13) que usa `wyoming==1.1.0`. |
| `requests`, `joblib`, `tqdm` | latest | Deps transitivas de openwakeword sin compilar. |

`pip install --no-deps` se usa para openwakeword y wyoming-openwakeword
porque sus dependencias de inferencia (tflite-runtime, onnxruntime, scipy)
**vienen de Termux `pkg`** (compiladas nativas para ARM, sin recompilar).

## Limitaciones de arquitectura

`wyoming-openwakeword` (el cliente Wyoming) y `openwakeword` se instalan
con pip. Pero las deps de inferencia tienen que ser ARM-native. En
Termux para ARMv7 (Huawei P8 Lite) la única opción funcional es la 1.10.0
del servidor Docker **o** el venv con estas versiones. Las versiones más
nuevas (openwakeword 0.6+) no tienen wheel para ARMv7 y habría que
compilar desde source, que tarda horas.

## Audio: `sles-source` vs `parec`

Para capturar audio en Android/Termux hay tres opciones:

- `sles-source` (nativo, OpenSL ES): el más estable, no necesita servidor
  PulseAudio. Usado en `boot/sles-source-loader.sh`.
- `parec` (PulseAudio CLI): más portable pero requiere `pulseaudio` corriendo
  en Termux, que es inestable en Android.
- `termux-microphone-record`: **NUNCA en scripts de boot**. Cuelga el
  proceso indefinidamente si Termux:API no está listo. Solo usar interactivamente.

Los 7 bugs resueltos del pipeline de audio Android están documentados en
`~/.claude/projects/-Users-alfonsogarre-Workspace-jota-voice/memory/feedback_audio_android.md`
(memoria del proyecto). Los que más afectan a OWW:

- `parec` lee del source sin crear `source-output` en PulseAudio → el
  monitor de Wyoming no ve el stream pero el audio llega correctamente.
- AudioFlinger queda corrupto tras `EACCES` o cuelgue de
  `termux-microphone-record` → reiniciar el móvil.

## Snippet de `install/04-oww.sh`

El script es el source of truth. Ver también el `commit fb93d47` y
anteriores en `install/`.
