# Fase 2 — Briefing de arranque (análisis, no implementación)

> **Propósito:** mapear a qué te enfrentas en la Fase 2 antes de empezar. Este documento **no compromete implementación**: describe alcance, dependencias, riesgos y un orden de ataque sugerido. Las decisiones de diseño ya cerradas están en `ROADMAP.md › Decisiones tomadas`.
>
> Generado el 2026-07-19 al cerrar la Fase 1.

---

## TL;DR

- **Tema de la fase:** *Resiliencia de red y protocolo*. Que el cliente se comporte de forma predecible ante red degradada/caída y deje de apoyarse en timeouts anidados que no hacen lo que aparentan.
- **Tamaño:** 9 issues (`#21`–`#29`), **todas `severity:high`**. Esfuerzo agregado: 1×L, 3×M, 3×S, 2×XS.
- **Acceptance gate (de ROADMAP):** simulación de red degradada (`tc netem` o similar) durante 20 turnos sin cuelgues; `is_silence()` unificado; cero secretos visibles en logs con `logging.level: DEBUG`.
- **La decisión grande ya tomada:** `#24` migra de *reconexión-por-turno* a *sesión WebSocket persistente* (ver Decisiones tomadas #1). Es el ítem estructural de la fase y del que cuelgan los enhancements 9.1 (barge-in real, `status` proactivo en IDLE).
- **⚠️ Colisión de secuenciación:** 3 issues de audio de esta fase (`#25`, `#27`, `#29`) **tocan los mismos ficheros** que el refactor planificado de *Fase A* (extracción del audio kit a `core/`, spec `2026-07-18-fase-a-audio-kit-design.md`). Hay que decidir el orden antes de tocar `backends/audio_*.py`. Ver §4.

---

## 1. Las 9 issues, por naturaleza

### A. Red / protocolo (el corazón de la fase)

| # | Título | Esf. | Dónde | Riesgo si no se hace |
|---|--------|------|-------|----------------------|
| **#24** `[016]` | Reconexión-por-turno contradice el protocolo (sesión persistente); `turn_seq` se parsea y se descarta | **L** | `gateway_client.py`, `state_machine.py` | Latencia extra de handshake+reauth en cada turno; sin `status` proactivo en IDLE; barge-in real inalcanzable |
| **#23** `[015]` | `recording_timeout_s` no es límite duro: `send_audio()` sin timeout por-frame puede colgarse | **S** | `state_machine.py::_capture_loop` | Con red "conectada pero muda" (buffer TCP lleno) el turno se cuelga > timeout; solo sale con `/cancel` manual |
| **#26** `[018]` | Timeouts triplemente anidados; `websockets.open_timeout` (10s) nunca se sobreescribe | **XS** | `gateway_client.py`, `state_machine.py` | Configurar `connect_timeout_s > 10s` es un no-op silencioso |

### B. Ciclo de vida / observabilidad

| # | Título | Esf. | Dónde | Riesgo si no se hace |
|---|--------|------|-------|----------------------|
| **#21** `[013]` | Shutdown no cancela tasks anidados: patrón `await wait(); cancel()` **sin `try/finally`** → tareas huérfanas al reiniciar a mitad de turno | **M** | `state_machine.py` (`_recording`, `_responding`, `_oww_loop`) | Excepciones logueadas *después* de "apagado limpiamente"; `gather(return_exceptions=True)` del shutdown nunca se inspecciona |
| **#22** `[014]` | Sin health-check ni feedback de OWW caído: reintenta en silencio, nunca publica al `EventBus` | **S** | `state_machine.py`, `oww_client.py` | Wake word muerta = icono en "idle" para siempre, sin señal para el usuario |

### C. Robustez de backends

| # | Título | Esf. | Dónde | Riesgo si no se hace |
|---|--------|------|-------|----------------------|
| **#25** `[017]` | `SounddeviceBackend.start()` sin reintentos si `sd.InputStream()` falla (Termux ya lo tiene) | **S** | `audio_sounddevice.py` | En boot vía launchd/systemd antes de que el audio esté listo, el proceso muere sin reintento |
| **#27** `[019]` | `is_silence()` duplicado y divergente (macOS con guard de array vacío, Termux sin él); preroll también copiado | **M** | `audio_sounddevice.py`, `audio_capture.py`, `audio_base.py` | Frame vacío en Termux se trata como "no silencio" (`nan < x == False`); causa estructural de bugs futuros |
| **#29** `[021]` | `audio_termux.py::stop()` no toma el lock que sí usan `_play()`/`drain()` | **XS** | `audio_termux.py` | Race real entre hilo de executor (PyAudio) y loop asyncio sobre el mismo stream durante shutdown |

### D. Seguridad / privacidad

| # | Título | Esf. | Dónde | Riesgo si no se hace |
|---|--------|------|-------|----------------------|
| **#28** `[020]` | Secretos en logs DEBUG: `client_key` y `CF-Access-Client-Secret` en claro (logger `websockets` hereda nivel raíz) | **XS** | `voice_client.py::_setup_logging`, `gateway_client.py` | Ya ocurrió en la práctica (ver `~/Library/Logs/jota-voice/stderr.log`). Fix: `getLogger("websockets").setLevel(INFO)` |

---

## 2. La decisión grande: `#24` sesión persistente

**Ya decidido (Decisiones tomadas #1): migrar.** No se re-discute salvo evidencia en contra. Puntos operativos:

- El protocolo del gateway está diseñado para **una sesión WS persistente con múltiples turnos**: `orchestrator`/`transcriber` reconectan en segundo plano sin cerrar el WS; el barge-in se resuelve comparando `turn_seq` en cada frame binario para descartar audio del turno anterior — **sin** reconectar.
- Hoy el cliente hace `connect()` al empezar cada `_recording()` y `disconnect()` en el `finally` de cada `_responding()`: WS nuevo + handshake completo **por turno**. `receive()` sí extrae `turn_seq` en cada `GatewayEvent`, pero **nada** en `state_machine.py` lo lee.
- **Habilita** (enhancements 9.1): barge-in real (`capabilities.barge_in`), notificaciones `status` proactivas durante IDLE, y elimina la latencia de reauth en el momento más sensible a la percepción de velocidad.
- **Riesgo registrado:** migrar puede romper el barge-in "repetir wake word" que hoy funciona. Mitigación del risk register: *feature flag* de arquitectura de sesión, manteniendo el camino por-turno como fallback hasta validar en campo.
- **Es el ítem de mayor esfuerzo (L)** y del que dependen 3 enhancements. Trátalo como sub-proyecto propio: merece su propio ciclo brainstorming → spec → plan (las tareas concretas de 9.1 se abren como issues al empezar).

> **Estado del gateway (tracking 2026-07-19):** el contrato del wire del flujo de audio/sesión **no ha cambiado**; el cliente es *compatible hacia delante*. El gateway endureció el enforcement de agentes (`default_agent`/`allowed_agents`, PR #154/#105) y añadió `request_id` y redacción de logs, pero nada de eso rompe a jota-voice hoy (el cliente no envía `agent`; `device_id` se ignora en silencio → confirma que `#83` es solo doc). Sí aparecen **oportunidades** relevantes para `#24`: el mensaje `ready` ya trae `session_id` y `capabilities.{barge_in,tts,transcriber}` que el cliente **descarta hoy** — capturarlos es prerequisito del barge-in real (enh 9.1). Detalle completo y issues propuestas (N1–N5) en el resumen de cierre de Fase 1. Confirmar el contrato vigente igualmente antes de diseñar la migración de `#24`.

---

## 3. Dependencias internas de la fase

- **`#26` antes que `#24`/`#23`:** el fix XS de `open_timeout` limpia la capa de timeouts sobre la que después se construye el manejo de red degradada. Barato y desbloquea razonar sobre el resto.
- **`#23` (timeout por-frame) y `#24` (sesión persistente) comparten `_capture_loop`/`_recording`:** conviene hacer `#23` como fix acotado *o* absorberlo dentro del rediseño de `#24`. Decidir para no tocar dos veces.
- **`#21` (shutdown/try-finally) es transversal:** toca los tres bucles de estado. Hacerlo pronto reduce ruido de logs al probar todo lo demás bajo red degradada (el gate de la fase estresa justo estos caminos).
- **`#22` (health-check OWW) + `#28` (redacción de logs):** independientes, sin dependencias, buenos "quick wins" de arranque.

---

## 4. ⚠️ Colisión con Fase A (extracción del audio kit)

Esto es lo que más cuidado requiere. La *Fase A* (spec `docs/superpowers/specs/2026-07-18-fase-a-audio-kit-design.md`, decisiones en memoria) planea mover VAD/preroll/framing/`audio_capture` a `client/core/` y dividir lo específico de Termux. Esa Fase A **está explícitamente bloqueada por el cierre de Fase 1** y **toca los mismos ficheros** que estas issues de Fase 2:

| Issue Fase 2 | Fichero | Relación con Fase A |
|---|---|---|
| **#27** `is_silence()` duplicado | `audio_sounddevice.py`, `audio_capture.py`, `audio_base.py` | **Fase A lo cierra por diseño** (clase base compartida en `core/`). Decisión Fase A #8: refactor puro, tests de regresión como red. |
| **#29** lock en `stop()` Termux | `audio_termux.py` | Bloqueante directo del refactor Fase A |
| **#25** reintentos en `start()` sounddevice | `audio_sounddevice.py` | Bloqueante directo del refactor Fase A |

**Decisión a tomar (tuya) al arrancar Fase 2 — dos caminos:**

1. **Fase A primero (recomendado por coherencia):** ejecutar el refactor a `core/` (que ya absorbe `#27` y prepara el terreno), y aplicar `#25`/`#29` sobre la estructura nueva. Evita arreglar en `backends/` algo que se va a mover en días. Coste: Fase A no es trivial y retrasa el resto de Fase 2.
2. **Bugfixes de red primero, audio después:** hacer el bloque de red/protocolo (`#26`, `#23`, `#24`, `#21`, `#22`, `#28`) sobre la estructura actual y dejar `#25`/`#27`/`#29` para cuando se haga Fase A. Ventaja: el corazón de la fase (resiliencia de red) no depende del refactor de audio en absoluto.

> Nota: la Decisión Fase A #7 exige mergear limpio `feature/universal-client` → `roadmap/fase-1-criticos` **antes** de empezar Fase A. Ese merge es prerequisito del camino 1.

---

## 5. Orden de ataque sugerido

Asumiendo el **camino 2** (red primero — desacopla del refactor de audio):

1. **`#28`** (XS, redacción de logs) — quick win de seguridad, cero dependencias.
2. **`#26`** (XS, `open_timeout`) — limpia la capa de timeouts.
3. **`#22`** (S, health-check OWW) — observabilidad, independiente.
4. **`#21`** (M, shutdown try/finally) — transversal; hacerlo antes de estresar red reduce ruido.
5. **`#23`** (S, timeout por-frame) — decidir si acotado o dentro de `#24`.
6. **`#24`** (L, sesión persistente) — sub-proyecto propio: brainstorming → spec → plan, con feature flag. Confirmar protocolo del gateway antes.
7. **Bloque audio (`#25`, `#27`, `#29`)** — ejecutar como/junto a Fase A, no sueltos en `backends/`.

Cada issue sigue el mismo workflow de Fase 1 (worktree aislado + TDD + code review por subagente + merge `--no-ff` local + ROADMAP + `gh issue close` + push + limpieza).

---

## 6. Riesgos a vigilar (del risk register + análisis)

- **Sin CI durante Fases 1–4:** los fixes se validan solo manualmente. Considerar adelantar `#41` (CI básico, Fase 4) si aparecen regresiones. El gate de Fase 2 (20 turnos con `tc netem`) es manual y caro de repetir.
- **`#24` puede romper el barge-in actual:** feature flag obligatorio; mantener camino por-turno como fallback.
- **`#27`/VAD:** cambiar el corte de silencio afecta producción; mantener el guard de array vacío como canónico y test de regresión con frames grabados reales.
- **El gate exige simular red degradada:** conviene preparar el arnés `tc netem` (o equivalente en macOS: `dnctl`/`pfctl`) como primera tarea de infraestructura de la fase.

---

## Referencias

- `docs/ROADMAP.md` › Fase 2, Decisiones tomadas, Risk register, Acceptance gates
- `docs/superpowers/specs/2026-07-18-fase-a-audio-kit-design.md` — refactor de audio (colisión §4)
- Issues `#21`–`#29`: https://github.com/Jota-project/jota-voice/issues?q=label:audit:2026-07-14
