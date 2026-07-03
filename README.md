# jota-voice

Capa de dispositivo del sistema de asistente de voz jota. Corre en Termux (Android).

Sustituye a `wyoming-satellite` + Home Assistant voice pipeline con un cliente streaming directo a `jota-gateway`, reduciendo la latencia de ~5-6s a <2s.

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
git clone <repo> ~/jota-voice && cd ~/jota-voice
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
| [`docs/spec.md`](docs/spec.md) | Especificación técnica: módulos, protocolos, máquina de estados |
| [`docs/plan.md`](docs/plan.md) | Plan de implementación por fases |
| [`docs/arquitectura.md`](docs/arquitectura.md) | Topología del sistema completo (legacy, mantener actualizado) |

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
devices/
  macbook_sito/               # config por dispositivo
  hab_sito/                   # config del Huawei (legacy)
deploy.sh                     # --target phone|macbook
config.example.yaml           # plantilla de configuración
```
