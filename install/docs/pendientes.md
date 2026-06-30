# Pendientes y roadmap

## Fase 1 — Bridge y pipeline mínimo funcional

- [ ] **Bridge OpenClaw↔REST en jota-gateway**
  - Implementar `POST /v1/chat/completions` que habla WS con OpenClaw
  - Implementar `GET /v1/models`
  - Ver detalles en [`openclaw-integracion.md`](openclaw-integracion.md)

- [ ] **Configurar integración OpenAI Conversation en HA**
  - Base URL: `http://green-house/api/gateway/v1`
  - Una vez el bridge esté activo

- [ ] **Instalar wyoming-openai en worker-01**
  - Proxy STT: Wyoming → `POST http://green-house/api/stt/v1/audio/transcriptions`
  - Verificar endpoint exacto de jota-transcriber (¿`/v1/audio/transcriptions` o `/transcribe`?)

- [ ] **Configurar Assist pipeline en HA**
  - STT: wyoming-openai
  - Conversation: OpenClaw (via bridge)
  - TTS: pendiente decisión (ver abajo)
  - Asignar al satélite Huawei P8 Lite

- [ ] **Arrancar jota-speaker :8005**
  - Verificar si tiene endpoint HTTP además de WebSocket
  - Configurarlo como TTS en el pipeline

- [ ] **Test de voz end-to-end**

## Fase 2 — Latencia y calidad de voz

- [x] **Wake word local en el teléfono** ✅ 2026-05-20
  - oww-venv en Termux con wyoming-openwakeword 1.8.2 + parches
  - Satélite usa `--wake-uri tcp://localhost:10401`
  - Elimina un round-trip de red completo

- [x] **Pre-roll de audio** ✅ 2026-05-20
  - Parche en `WakeStreamingSatellite` — ring buffer de 1.5s
  - El STT recibe la wake word + todo lo dicho al instante, sin esperar beep

- [x] **Threshold openwakeword 0.3** ✅ 2026-05-20
  - Reducido de 0.5 a 0.3 para detección más rápida

- [x] **Infraestructura kiosk** ✅ 2026-05-21
  - `kiosk-server` en worker-01 (systemd user service) — ver `docs/kiosk.md`
  - ADB WiFi desde worker-01 controla pantalla del teléfono
  - Hooks wyoming-satellite disparan estados → pantalla + UI web
  - UI web en `kiosk/index.html`: orb animado, barras de sonido, puntos pensando

- [ ] **Kiosk — página accesible sin aceptar certificado** ⬅️ PRÓXIMO
  - El servidor HTTPS usa cert autofirmado → Chrome muestra warning
  - Solución A: usuario acepta el cert una vez en Chrome y añade PWA a inicio
  - Solución B: proxy por nginx de green-house (ya tiene SSL válido)
  - URL actual: `https://<IP_ASISTENTE>:8443/`

- [ ] **Kiosk — apagado automático de pantalla tras respuesta**
  - Ya implementado en `kiosk_server.py`: `AUTO_SLEEP_SECONDS = 8`
  - `schedule_sleep()` llama a `adb shell input keyevent KEYCODE_SLEEP` tras 8s
  - Pendiente: verificar que funciona end-to-end una vez la página esté accesible

- [ ] **Kiosk — interrumpir TTS con wake word**
  - Actualmente `wake_refractory_seconds=5.0` bloquea detección durante TTS
  - Para permitir interrupción: añadir `--wake-refractory-seconds 0` en `start-satellite.sh`
  - HA cancela el pipeline actual al recibir un nuevo `run-pipeline` (comportamiento nativo)
  - Riesgo: falsos positivos durante el audio de respuesta (el altavoz puede activar la wake word)

- [ ] **TTS streamed**
  - jota-speaker recibe tokens del LLM según llegan (ya tiene el protocolo)
  - Reduce latencia percibida

## Fase 3 — Calidad y robustez (renombrada, antes Fase 2)

- [ ] **Skill de contexto voz en OpenClaw**
  - Respuestas cortas, sin markdown, para interacciones de voz
  - HA añade `[voice]` al contexto del mensaje

- [ ] **Wake word fiable**
  - Verificar latencia ok_nabu → respuesta completa

- [ ] **TTS streamed**
  - jota-speaker recibe tokens del LLM según llegan (ya tiene el protocolo)
  - Reduce latencia percibida

- [ ] **Enrutamiento TTS**
  - Teléfono para respuestas rápidas / personales
  - Google Home Mini para anuncios del hogar

- [ ] **Reserva DHCP para green-house**
  - Fijar MAC de `eno2` en el router para IP estable
  - Evitar que cambie y rompa conexiones LAN

## Fase 3 — Integración profunda OpenClaw + HA

- [ ] **Skill HA en OpenClaw**
  - OpenClaw controla HA directamente (luces, sensores, alarmas)
  - Requiere long-lived access token de HA en el skill

- [ ] **Skill Cast en OpenClaw**
  - Reproducir en Google Home Mini via Cast desde el agente

- [ ] **Memoria de sesión de voz**
  - OpenClaw ya tiene MEMORY.md y daily notes
  - Conectar contexto de voz con el mismo historial

## Pendiente de confirmar

- [ ] Endpoint exacto de jota-transcriber para HTTP one-shot: `POST /v1/audio/transcriptions` o `/transcribe`?
- [ ] ¿jota-speaker tiene endpoint HTTP además del WebSocket?
- [ ] Token de auth de OpenClaw para el bridge (leer de `~/.openclaw/openclaw.json`)
- [ ] ¿El bridge necesita autenticarse con OpenClaw o el túnel local es confiable?
