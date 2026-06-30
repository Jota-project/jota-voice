# OpenClaw — Integración con Home Assistant

## Lo que OpenClaw ES (y lo que NO es)

OpenClaw es un **gateway de agente de IA** con canales (WhatsApp, Telegram, WebChat…).  
Su API en `:18789` es un **protocolo WebSocket propietario**, no una API REST OpenAI-compatible.

**No existe `/v1/chat/completions` en el gateway de OpenClaw.** El documento de arquitectura original lo indicaba incorrectamente.

---

## Protocolo WebSocket de OpenClaw

```
Cliente conecta WS a ws://127.0.0.1:18789/
       ▼
Gateway envía challenge:
  {"type":"event","event":"connect.challenge","payload":{"nonce":"<uuid>","ts":<timestamp>}}
       ▼
Cliente responde con auth (token firmado con nonce)
       ▼
Intercambio de mensajes:
  Petición: {"type":"req", ...}
  Respuesta: {"type":"event", ...}
```

El token de auth está en `~/.openclaw/openclaw.json` → `gateway.auth`.

---

## Acceso desde el Mac

La app nativa de OpenClaw para macOS abre automáticamente un SSH tunnel:
```
ssh -L 18789:127.0.0.1:18789 usuario@tu-servidor-ssh.com
```
Luego accede al dashboard en `http://127.0.0.1:18789/` localmente.  
No requiere ninguna configuración manual.

---

## Acceso desde la LAN

nginx expone OpenClaw en `http://green-house/api/openclaw/` (HTTP, sin SSL).  
Esto es suficiente para scripts internos, herramientas de la LAN y futuros clientes.

---

## Integración con Home Assistant — El bridge

HA necesita un endpoint OpenAI REST (`POST /v1/chat/completions`) para su integración de conversación.  
OpenClaw no lo expone nativamente. Solución: **`jota-gateway` (puerto 8004) actúa como bridge**.

### Arquitectura del bridge

```
HA (worker-01)
  POST http://green-house/api/gateway/v1/chat/completions
       ▼
jota-gateway :8004
  recibe JSON OpenAI-compatible
  abre WS a OpenClaw :18789
  envía mensaje
  recibe respuesta
  devuelve JSON OpenAI-compatible
       ▼
HA recibe la respuesta de OpenClaw
```

### Lo que el bridge debe implementar

```
POST /v1/chat/completions
  Body: {model, messages, stream?}
  Response: OpenAI ChatCompletion format

GET /v1/models
  Response: lista de modelos disponibles
```

Internamente, jota-gateway:
1. Abre WebSocket a `ws://127.0.0.1:18789`
2. Resuelve el challenge con el token de OpenClaw
3. Envía el último mensaje del array `messages`
4. Espera la respuesta del agente
5. Formatea como `ChatCompletion` y devuelve

Para streaming (`stream: true`), cada token del agente se emite como SSE (`data: {delta...}`).

---

## Capacidades de integración una vez el bridge esté activo

| Capacidad | Disponible |
|---|---|
| Conversación natural con memoria | ✅ (OpenClaw mantiene historial) |
| Personalidad / SOUL.md | ✅ |
| Skills de OpenClaw (web, tools…) | ✅ |
| Respuestas cortas para voz | ✅ via skill de contexto voz |
| Control de HA (luces, sensores…) | ✅ via skill OpenClaw + HA REST API |
| Barge-in (interrumpir respuesta) | ⚠️ depende de TTS/wyoming |

### Skill de contexto voz

Para que OpenClaw sepa que está respondiendo a una petición de voz (respuestas cortas, sin markdown):

```markdown
# Voice Context Skill
Cuando el campo `user` del mensaje incluya `[voice]` o el canal sea `voice`,
responde en máximo 2 frases. Sin listas, sin markdown, sin emojis.
```

HA puede añadir `[voice]` al principio del texto transcrito antes de enviarlo al bridge.

### Skill de control HA

OpenClaw puede llamar a la REST API de HA directamente con un skill que incluya:
- URL de HA: `http://worker-01.local:8123`
- Long-lived access token (generado en HA → Perfil → Tokens)
- Tool calls a `/api/services/light/turn_on`, `/api/states`, etc.

---

## Configuración en Home Assistant

Una vez el bridge esté activo, en HA:

1. **Settings → Devices & Services → Add Integration → "OpenAI Conversation"**
2. API Key: el token de OpenClaw (o un dummy si el bridge no lo requiere)
3. Base URL: `http://green-house/api/gateway/v1`
4. Model: el que devuelva `/v1/models` del bridge

Luego en **Settings → Voice Assistants → Add Assistant**:
- STT: wyoming-openai (proxy hacia jota-transcriber)
- Conversation: OpenClaw (la integración que acabas de añadir)
- TTS: el que esté disponible
- Asignar al satélite (Huawei P8 Lite)
