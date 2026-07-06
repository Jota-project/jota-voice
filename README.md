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
# En el teléfono (Termux)
git clone https://github.com/Jota-project/jota-voice.git ~/jota-voice && cd ~/jota-voice
sh install.sh
nano config.yaml          # rellenar client_key y IPs
python client/voice_client.py config.yaml

# Desde el Mac — deploy
cp .env.local.example .env.local && nano .env.local
bash deploy.sh
```

### macOS (MacBook)

```bash
# Una vez por máquina
REPO_DIR=$(pwd) bash install/macos/01-homebrew.sh   # brew + python + docker check
REPO_DIR=$(pwd) bash install/macos/03-venv.sh       # ~/venvs/jota-voice
REPO_DIR=$(pwd) bash install/macos/04-oww.sh         # Wyoming en Docker (puerto 10401)
REPO_DIR=$(pwd) bash install/macos/06-configs.sh     # symlink config
REPO_DIR=$(pwd) bash install/macos/07-launchd.sh     # launchd agent

# Deploys posteriores
bash deploy.sh macbook
```

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
  voice_client.py             # entry point — wire registry + tasks
  state_machine.py            # máquina de estados IDLE→RECORDING→RESPONDING
  event_bus.py                # pub/sub asíncrono
  gateway_client.py           # WebSocket jota-gateway (handshake con device_id)
  playback_engine.py          # orquesta TTS delegando en AudioBackend
  config.py                   # carga config.yaml (Pydantic v2)
  control_server.py           # HTTP /cancel para jota-display
  oww_client.py               # Wyoming TCP client (helper de WyomingBackend)
  audio_capture.py            # parec/PulseAudio (helper de TermuxBackend)
  display_client.py           # EventBus → DisplayBackend (inyectado)
  backends/                   # interfaces intercambiables por SO
    registry.py               # factory con auto-detección por sys.platform
    audio_base.py             # Protocol AudioBackend
    audio_sounddevice.py      # Mac/Linux (sounddevice/PortAudio)
    audio_termux.py           # Android (parec + pyaudio)
    display_base.py           # Protocol DisplayBackend
    display_http.py           # POST a jota-display
    display_null.py           # no-op
    oww_base.py               # Protocol OwWBackend
    oww_wyoming.py            # wrapper sobre OWWClient
  test_*.py                   # tests unitarios
install/
  shared/99-smoke-test.sh     # smoke test automatizado cross-platform
  termux/                     # scripts de instalación en Android (legacy)
  macos/                      # scripts de instalación en macOS
  docs/                       # documentación técnica (spec, arquitectura, kiosk, nginx...)
devices/
  macbook_sito/                # config macOS (directorio + config.yaml)
  hab_sito.env                 # config del teléfono Huawei (legacy, fichero plano)
deploy.sh                     # --target phone|macbook
config.example.yaml           # plantilla de configuración
```
