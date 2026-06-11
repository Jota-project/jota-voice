# jota-voice

Capa de dispositivo del sistema de asistente de voz jota. Corre en Termux (Android).

Sustituye a `wyoming-satellite` + Home Assistant voice pipeline con un cliente streaming directo a `jota-gateway`, reduciendo la latencia de ~5-6s a <2s.

## Arquitectura

```
[Teléfono - Termux]
  wyoming-openwakeword (worker-01:10401) ← wake word
         ↓ Detection
  jota-voice-client ───── WS streaming ──→ jota-gateway :8004
         ↓                                   ├── jota-transcriber (STT)
  jota-display :8766                         ├── OpenClaw (LLM)
  (UI kiosk)                                 └── jota-speaker (TTS)
                                                  ↓ audio chunks back
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

## Documentación

| Fichero | Contenido |
|---|---|
| [`docs/spec.md`](docs/spec.md) | Especificación técnica: módulos, protocolos, máquina de estados |
| [`docs/plan.md`](docs/plan.md) | Plan de implementación por fases |
| [`docs/arquitectura.md`](docs/arquitectura.md) | Topología del sistema completo (legacy, mantener actualizado) |

## Estructura

```
client/
  voice_client.py   # proceso principal — máquina de estados
  oww.py            # cliente wyoming-openwakeword
  gateway.py        # cliente WebSocket jota-gateway
  audio.py          # captura/reproducción PulseAudio
  display.py        # estado → jota-display (best-effort)
  config.py         # carga config.yaml con Pydantic
boot/
  start.sh          # arranca el cliente (Termux:Boot)
install.sh          # primer setup en dispositivo nuevo
deploy.sh           # sync + restart desde el Mac
config.example.yaml # plantilla de configuración
```
