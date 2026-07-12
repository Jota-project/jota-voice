# jota-voice

Cliente Android/Termux del ecosistema [Jota](https://github.com/Jota-project), un asistente de voz distribuido y modular.

Sustituye a `wyoming-satellite` + Home Assistant voice pipeline con streaming directo a [`jota-gateway`](https://github.com/Jota-project/jota-gateway), reduciendo la latencia de ~5-6s a <2s. Corre en el mismo dispositivo que [`jota-display`](https://github.com/Jota-project/jota-display) (UI kiosk).

Ver la [arquitectura completa del ecosistema](https://github.com/Jota-project/.github/blob/main/ARCHITECTURE.md) para el resto de microservicios (gateway, transcriber, speaker, orchestrator...).

## Arquitectura

```
                config.yaml
                     │
                     ▼
            ┌──────────────┐
            │   registry   │  ← auto-detecta SO
            └──────┬───────┘
                   │
   ┌───────────────┼───────────────┐
   ▼               ▼               ▼
AudioBackend   DisplayBackend   OwWBackend
   │               │               │
   ├─Sounddevice  ├─Http          └─Wyoming (TCP)
   │  (Mac/Linux) └─Null
   └─Termux        (no-op)
     (Android)
```

## Setup rápido

```bash
# En el teléfono (Termux) — primera vez
git clone https://github.com/Jota-project/jota-voice.git ~/jota-voice && cd ~/jota-voice
cp config.example.yaml devices/<id>/config.yaml && nano devices/<id>/config.yaml   # client_key, IPs
sh install.sh              # install/06-configs.sh crea el symlink config.yaml → devices/<id>/config.yaml
python client/voice_client.py config.yaml

# Desde el Mac — deploy a un dispositivo ya configurado
cp devices/example.env devices/<nombre>.env && nano devices/<nombre>.env   # credenciales SSH
bash deploy.sh phone <nombre>
```

`devices/<id>/config.yaml` nunca se trackea en git (contiene el `client_key`
real) — cada usuario/dispositivo crea el suyo a partir de
`config.example.yaml`. `devices/<nombre>.env` tampoco se trackea — son las
credenciales SSH que usa `deploy.sh` para llegar a ese dispositivo desde el
Mac.

### macOS (MacBook)

```bash
# Una vez por máquina
REPO_DIR=$(pwd) bash install/macos/01-homebrew.sh   # brew + python
REPO_DIR=$(pwd) bash install/macos/03-venv.sh       # ~/venvs/jota-voice
REPO_DIR=$(pwd) bash install/macos/04-oww.sh         # Wyoming OWW nativo (venv + launchd, puerto 10401)
REPO_DIR=$(pwd) bash install/macos/06-configs.sh     # symlink config
REPO_DIR=$(pwd) bash install/macos/07-launchd.sh     # launchd agent para voice_client

# Deploys posteriores
bash deploy.sh macbook
```

> **Wyoming OpenWakeWord:** el servidor OWW corre nativo (venv Python
> supervisado por launchd), no en Docker. Usa el paquete oficial
> `wyoming-openwakeword==1.8.2` con un shim local que sustituye
> `tflite-runtime-nightly` por `ai-edge-litert` (la primera no publica
> wheels para macOS; ver `install/macos/tflite_runtime_shim/`).

### Barra de menú nativa

Al instalar jota-voice en macOS aparece automáticamente un icono en la barra de menús superior. El icono cambia según el estado del cliente:

| Estado | Icono (SF Symbol) | Significado |
|---|---|---|
| `idle` | `mic` | Esperando wake word |
| `listening` | `ear` | Grabando |
| `thinking` | `brain` | Esperando respuesta del gateway |
| `speaking` | `speaker.wave.2` | Reproduciendo TTS |
| `error` | `exclamationmark.triangle` | Último turno terminó en error |

El menú incluye:

- Cabecera con el estado actual (no seleccionable).
- Submenú **Servicio** → Pausar/Reanudar escucha, Apagar servicio.
- Abrir logs / Abrir configuración en la app por defecto.
- Acerca de / Salir.

Para desactivar el UI sin desinstalar pyobjc: `export JOTA_DISABLE_MENUBAR=1` antes de arrancar el cliente (o añade esa línea a tu `.jota-voice.env`).

Logs: `tail -f ~/Library/Logs/jota-voice/stdout.log`.

Servicio: `launchctl list | grep com.jota.voice`.

## Documentación

| Fichero | Contenido |
|---|---|
| [`install/docs/spec.md`](install/docs/spec.md) | Especificación técnica: módulos, protocolos, máquina de estados |
| [`install/docs/arquitectura.md`](install/docs/arquitectura.md) | Topología del sistema completo (legacy, mantener actualizado) |
| [`install/docs/pendientes.md`](install/docs/pendientes.md) | Roadmap y tareas pendientes |
| [`install/docs/openclaw-integracion.md`](install/docs/openclaw-integracion.md) | Integración OpenClaw ↔ Home Assistant |
| [`install/docs/kiosk.md`](install/docs/kiosk.md) | Kiosk de voz — legacy (Huawei P8 Lite) |
| [`install/docs/nginx.md`](install/docs/nginx.md) | Proxy inverso en green-house |
| [`install/docs/bootstrap-setup.md`](install/docs/bootstrap-setup.md) | Instalación manual de APKs (una vez por teléfono) |
| [`install/docs/fullyKiosk-setup.md`](install/docs/fullyKiosk-setup.md) | Setup manual de FullyKiosk Browser |
| [`install/docs/parches-movil.md`](install/docs/parches-movil.md) | Parches aplicados a librerías de terceros en el móvil |

## Estructura

```
client/
  config.py                   # carga config.yaml (dataclasses)
  domain/                      # lógica de negocio, sin I/O directo
    state_machine.py           # máquina de estados IDLE→RECORDING→RESPONDING
    event_bus.py                # pub/sub asíncrono
  app/                          # orquestación — wiring de backends + entry point
    voice_client.py             # entry point — wire registry + tasks
    playback_engine.py          # orquesta TTS delegando en AudioBackend
    display_client.py           # EventBus → DisplayBackend (inyectado)
    control_server.py           # HTTP /cancel para jota-display
  backends/                    # interfaces intercambiables por SO
    registry.py                 # factory con auto-detección por sys.platform
    gateway_client.py           # WebSocket jota-gateway (handshake con device_id)
    audio_base.py                # Protocol AudioBackend
    audio_sounddevice.py         # Mac/Linux (sounddevice/PortAudio)
    audio_termux.py              # Android (parec + pyaudio)
    audio_capture.py             # parec/PulseAudio (helper de TermuxBackend)
    display_base.py              # Protocol DisplayBackend
    display_http.py              # POST a jota-display
    display_null.py              # no-op
    oww_base.py                  # Protocol OwWBackend
    oww_wyoming.py                # wrapper sobre OWWClient
    oww_client.py                 # Wyoming TCP client (helper de WyomingBackend)
  tests/{domain,app,backends}/  # tests unitarios, mismo layout que el código
install/
  shared/99-smoke-test.sh     # smoke test automatizado cross-platform
  shared/tests/                # tests de los scripts de install/shared
  termux/                     # scripts de instalación en Android (legacy)
  macos/                      # scripts de instalación en macOS
  docs/                       # documentación técnica (spec, arquitectura, kiosk, nginx...)
devices/
  <id>/config.yaml             # identidad real de un dispositivo (gitignored, una por Mac/Termux)
  <id>/.env                    # secretos por dispositivo: p.ej. cabeceras de Cloudflare Access (gitignored)
  <nombre>.env                 # credenciales SSH para deploy.sh phone <nombre> (gitignored)
  example.env                  # plantilla de <nombre>.env (trackeada)
deploy.sh                     # phone [<nombre>] | macbook
config.example.yaml           # plantilla de devices/<id>/config.yaml (trackeada)
```
