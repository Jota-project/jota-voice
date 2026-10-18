# jota-voice — Arquitectura del sistema

## Objetivo

Huawei P8 Lite (LineageOS 18.1) como satélite de voz en Home Assistant, usando **OpenClaw** como cerebro del asistente, **jota-transcriber** como STT y **jota-speaker** como TTS.

---

## Inventario de servicios

| Servicio | Host | Puerto | Estado | Descripción |
|---|---|---|---|---|
| **wyoming-satellite** | <IP_TELEFONO> (teléfono) | 10700 | ✅ | Captura micrófono, Wyoming Protocol |
| **wyoming-openwakeword** | mi-asistente.local (Docker) | 10400 | ✅ | Wake word "ok nabu" |
| **Home Assistant** | mi-asistente.local | 8123 | ✅ | Orquestador domótico, detecta el satélite |
| **nginx** | mi-servidor | 80 / 443 | ✅ | Proxy inverso único para todos los servicios |
| **OpenClaw gateway** | mi-servidor | 18789 | ✅ | Cerebro del asistente. WebSocket propietario. Solo loopback. |
| **jota-transcriber** | mi-servidor | 8003 | ✅ | STT C++/whisper.cpp |
| **jota_db_api** | mi-servidor | 8002 | ✅ | API BD conversacional |
| **jota-gateway (BFF)** | mi-servidor | 8004 | ⚠️ | BFF — será el bridge OpenClaw↔OpenAI REST |
| **jota-speaker** | mi-servidor | 8005 | ❌ | TTS streaming (parado) |
| **Ollama** | mi-servidor | 11434 | ✅ | LLM local. Modelos: Qwen3.5-4B (x2) |
| **Google Home Mini** | <IP_ALTAVOZ> | — | ✅ | Altavoz Cast (en HA) |

> **mi-servidor** = <IP_SERVIDOR> (IP dinámica, DNS local resuelve `mi-servidor`)  
> Acceso externo vía Cloudflare Tunnel → `mi-servidor.mi-dominio.com`

---

## Topología de red

```
[Teléfono - <IP_TELEFONO>]
  wyoming-satellite :10700
       │ Wyoming Protocol
       ▼
[mi-asistente.local]
  Home Assistant :8123
       │ Assist pipeline
       ├──► STT  ──► http://mi-servidor/api/stt/
       │              └── nginx → jota-transcriber :8003
       │
       ├──► LLM  ──► http://mi-servidor/api/openclaw/v1/chat/completions  ← PENDIENTE bridge
       │              └── nginx → OpenClaw :18789 (hoy solo WS, necesita bridge)
       │
       └──► TTS  ──► jota-speaker :8005 (parado) / ElevenLabs / HA built-in

[mi-servidor - <IP_SERVIDOR>]
  nginx :80 / :443 → proxy inverso
  OpenClaw gateway :18789 (loopback)
  jota-transcriber :8003 (0.0.0.0)
  jota_db_api :8002 (0.0.0.0)
  Ollama :11434
```

---

## Proxy inverso nginx

Un solo punto de entrada en mi-servidor. Rutas definidas en `/etc/nginx/includes/api-locations.conf`, incluido en bloques HTTP (`:80`) y HTTPS (`:443`).

| Ruta | Puerto interno | Servicio |
|---|---|---|
| `/api/jota/ws/` | :8000 | JotaOrchestrator WebSocket |
| `/api/jota/` | :8000 | JotaOrchestrator REST |
| `/api/inference/` | :8001 | InferenceCenter |
| `/api/db/` | :8002 | JotaDB |
| `/api/stt/` | :8003 | jota-transcriber |
| `/api/gateway/` | :8004 | jota-gateway BFF |
| `/api/metrics/` | :3000 | Métricas |
| `/api/openclaw/` | :18789 | OpenClaw gateway |

No hay redirect 80→443 — tráfico LAN usa HTTP directamente.  
Tráfico externo llega via Cloudflare Tunnel (HTTPS terminado en el edge de CF).

Ver detalle en [`nginx.md`](nginx.md).

---

## Flujo de voz completo (objetivo)

```
1. "ok nabu"
   └── wyoming-openwakeword (mi-asistente) detecta wake word

2. HA envía RunPipeline al satélite
   └── wyoming-satellite captura audio PCM

3. STT
   └── HA → POST http://mi-servidor/api/stt/v1/audio/transcriptions
       └── jota-transcriber devuelve texto

4. LLM
   └── HA → POST http://mi-servidor/api/openclaw/v1/chat/completions
       └── nginx → jota-gateway (bridge) → OpenClaw WebSocket → respuesta

5. TTS
   └── jota-speaker :8005 → PCM16 → wyoming-satellite
       O bien: ElevenLabs → Google Home Mini via Cast

6. Audio reproducido
```

---

## Acceso a OpenClaw

**Desde el Mac (app nativa):** SSH tunnel automático → `localhost:18789`  
**Desde la LAN (HA, scripts):** `http://mi-servidor/api/openclaw/` (nginx proxy)  
**Externo:** no configurado intencionalmente — no necesario

OpenClaw usa protocolo WebSocket propietario con challenge/response.  
Ver detalles en [`openclaw-integracion.md`](openclaw-integracion.md).

---

## Cloudflare Tunnel

Configurado en `/etc/cloudflared/config.yml` en mi-servidor.

| Hostname | Destino |
|---|---|
| `mi-servidor.mi-dominio.com` | `https://127.0.0.1:443` (nginx) |
| `ssh.mi-dominio.com` | `ssh://127.0.0.1:22` |

`j.mi-dominio.com` existe en DNS de CF pero no tiene regla en el túnel — no se usa.
