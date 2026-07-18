# jota-voice Roadmap

> **Estado:** 🔧 En remediación (post auditoría 2026-07-14)
> **Última actualización:** 2026-07-18
> **Issues abiertas:** 94 (rango GitHub `#9`–`#102`)
> **Rama de trabajo:** `feature/universal-client`
> **Próximo release:** al cerrar Fase 1 (críticos)

Este documento es el **plan vivo de remediación y evolución** de jota-voice. Cada tarea referencia una issue de GitHub; las casillas se tachan al cerrar la issue. Se actualiza en el mismo PR que cierra la issue, o en un PR dedicado.

**Estado de las issues:** https://github.com/Jota-project/jota-voice/issues?q=is:open+label:audit:2026-07-14
**Repo hermano:** [jota-gateway/docs/ROADMAP.md](https://github.com/Jota-project/jota-gateway/blob/main/docs/ROADMAP.md) — mismo formato, auditoría análoga del BFF.

---

## TL;DR

| Métrica | Valor |
|---|---|
| Issues totales | **94** |
| 🔴 Críticos | 12 |
| 🟠 Altos | 30 |
| 🟡 Medios | 23 |
| ⚪ Tech-debt / polish | 29 |
| Estimación | ~9-12 semanas |
| Próximo milestone | Cerrar Fase 1 (críticos) |
| Ya arreglado antes de la auditoría | 1 (clipping de audio en `audio_sounddevice.py`, pendiente comitear) |

---

## Estado actual (baseline)

El cliente funciona en **happy path** (mac con permisos correctos, red estable, gateway sano) pero tiene **bugs críticos latentes** que se manifiestan en el uso normal, no solo en condiciones extremas — el más grave (issue #9) hace que el pipeline de audio se degrade en **cualquier turno**, no en un edge case. También hay **huecos de seguridad reales** (ControlServer sin auth, secretos en logs DEBUG), **huecos de producción** (sin CI, dependencias de producción no declaradas, permisos de secretos que ya se degradaron en disco), y una **arquitectura de reconexión que contradice el protocolo del gateway** (reconecta por turno en vez de sesión persistente, dejando inutilizado el mecanismo de barge-in real que el protocolo ya ofrece).

### Hallazgos más graves (ver Fase 1)

- La cola de audio compartida entre detección de wake word (OWW) y grabación hace que ambos consumidores se roben frames mutuamente en **cualquier** turno — degrada transcripción y detección de wake word a la vez, y el propio mecanismo de "wake word interrumpe TTS" se sabotea a sí mismo.
- `ControlServer` (127.0.0.1:8765) no tiene autenticación — atacable desde JS de cualquier pestaña de navegador abierta (sin preflight CORS).
- `menubar_cocoa.py::stop()` mata el proceso completo de forma abrupta (`terminate:`) en carrera con el shutdown ordenado de asyncio — confirmado con reproducción empírica real en pyobjc.
- Un `ConnectionError` del lado de envío de audio en `oww_client.py::run_forever` escapa del manejo de excepciones y mata la detección de wake word **permanentemente** tras cualquier caída de red ordinaria.

### Ya arreglado (no requiere issue)

- Clipping/huecos en la reproducción de audio (`audio_sounddevice.py::_enqueue_and_wait`) — race de arranque de stream + margen de sleep incorrecto. Arreglado en el working tree de `feature/universal-client` con tests; **pendiente comitear**.

---

## Fases de remediación

### 🔴 Fase 1 — Bugs críticos (semanas 1–2)

**Objetivo:** cerrar los 12 bugs que rompen el producto en uso normal o exponen una superficie de ataque real.
**Acceptance gate:** cero issues 🔴 abiertas, suite de tests verde, una sesión de prueba manual de 10 turnos consecutivos sin degradación de audio perceptible, `ControlServer` rechaza peticiones sin token.

- [x] **#9** 🔴 `[001]` — Cola de audio compartida entre OWW y captura — consumidores en competencia real — **M** — fan-out a colas independientes (`e124a50`, `746caf0`, `283fd75`)
- [x] **#10** 🔴 `[002]` — ControlServer (127.0.0.1:8765) sin autenticación — vulnerable desde cualquier pestaña de navegador — **S** — token compartido (600) + header `X-Jota-Control-Token` + rate limiting (`c27ba2a`, `a31fd42`)
- [x] **#11** 🔴 `[003]` — Fallo de `audio.start()` en macOS deja el icono del menubar "vivo" sin que nada funcione — **S** — try/except visible en menubar (`set_state("error")`) + "Salir" siempre cierra la app (`00bc2f6`, `984b387`)
- [x] **#12** 🔴 `[004]` — Excepción de `capture_task` durante RECORDING nunca se comprueba — **S** — propagar `capture_task.exception()` en `_recording()` reusando el patrón de `_responding()` (`c245aa9`)
- [x] **#13** 🔴 `[005]` — `audio_termux.py::_play()` pierde muestras en chunks de longitud impar — **S** — arrastrar byte suelto en `_play()` y reset en `stop`/`drain`/`reset`, replicando el patrón de `audio_sounddevice.py::_enqueue_carry` (`b48ff57`)
- [x] **#14** 🔴 `[006]` — `oww_client.py`: rate/channels hardcodeados a 16000/mono, desacoplados de `AudioConfig` — **S** — inyectar `AudioConfig` (sample_rate/channels) en `OWWClient` y `WyomingBackend` para que los eventos `audio-start`/`audio-chunk` del protocolo Wyoming reflejen el rate/channels reales del micrófono en vez de los hardcodeados (`940815e`)
- [x] **#15** 🔴 `[007]` — `gateway_client.py::connect()` no espera el mensaje `ready` antes de enviar audio — **S** — leer + validar primer mensaje tras handshake (`ready`/`error`/inesperado); `ConnectionClosed(1008)` propagado sin capturar; `cfg.connect_timeout_s` acotando la espera; 6 nuevos tests (`1cd78d8`, `45a565e`)
- [x] **#16** 🔴 `[008]` — Cierre de conexión a mitad de turno se trata igual que un fin de turno normal — **S** — distinguir `ConnectionClosedOK` (silencioso) vs `ConnectionClosedError` (propaga y publica `error`); `max_size=None` en `websockets.connect()` para no auto-cierre 1009 por frame binario TTS grande (`3b365e1`, `8a94850`)
- [ ] **#17** 🔴 `[009]` — `oww_client.run_forever`: un `ConnectionError` del envío escapa y mata la detección de wake word por completo — **M**
- [ ] **#18** 🔴 `[010]` — Mensajes `status`/`error` del protocolo del gateway nunca se manejan — **S**
- [ ] **#19** 🔴 `[011]` — `menubar_cocoa.py::stop()` mata el proceso entero de forma abrupta — **S**
- [ ] **#20** 🔴 `[012]` — SIGINT/SIGTERM solo se procesan cuando dispara el NSTimer — latencia de hasta 1s — **M**

### 🟠 Fase 2 — Resiliencia de red y protocolo (semana 3)

**Objetivo:** que el cliente se comporte de forma predecible ante red degradada/caída, y no se apoye en timeouts anidados que no hacen lo que aparentan.
**Acceptance gate:** simulación de red degradada (`tc netem` o similar) durante 20 turnos sin cuelgues; `is_silence()` unificado; ningún secreto visible en logs con `logging.level: DEBUG`.

- [ ] **#21** 🟠 `[013]` — Shutdown no cancela realmente los tasks anidados — proceso reiniciado a mitad de turno deja tareas huérfanas — **M**
- [ ] **#22** 🟠 `[014]` — Sin health-check ni feedback de que OWW (wake word) está caído — **S**
- [ ] **#23** 🟠 `[015]` — `recording_timeout_s` no es un límite duro frente a una conexión de red degradada — **S**
- [ ] **#24** 🟠 `[016]` — Arquitectura de reconexión por turno contradice el protocolo (diseñado para sesión persistente) — `turn_seq` muerto — **L** — ✅ *decidido: migrar a sesión persistente, ver "Decisiones tomadas"*
- [ ] **#25** 🟠 `[017]` — `audio_sounddevice.py::start()` sin reintentos si `sd.InputStream()` falla al abrir — **S**
- [ ] **#26** 🟠 `[018]` — Timeouts de conexión triplemente anidados — el más interno (websockets, 10s) nunca se sobreescribe — **XS**
- [ ] **#27** 🟠 `[019]` — `is_silence()` duplicado y divergente entre `audio_sounddevice.py` y `audio_capture.py` — **M**
- [ ] **#28** 🟠 `[020]` — Secretos en logs DEBUG sin ninguna mitigación (`client_key`, `CF-Access-Client-Secret`) — **XS**
- [ ] **#29** 🟠 `[021]` — `audio_termux.py::stop()` no usa el lock que sí usan `_play()`/`drain()` — **XS**

### 🟠 Fase 3 — UI/menubar y ciclo de vida del proceso (semana 4)

**Objetivo:** que el menubar no pueda tumbar ni bloquear el resto del cliente, y que el composition root (`voice_client.py`) tenga red de seguridad de tests.
**Acceptance gate:** apagar el servicio desde el menú no bloquea el run loop más de 200ms; un fallo de construcción del menubar no impide que arranque el resto del cliente; `voice_client.py` tiene al menos un test de integración end-to-end.

- [ ] **#30** 🟠 `[022]` — `_shutdown_service()` bloquea el hilo principal de Cocoa hasta 10s con `subprocess.run` síncrono — **XS**
- [ ] **#31** 🟠 `[023]` — Race condition confirmada en `_pending_idle_task` del menubar — el icono revierte a "idle" espontáneamente — **XS**
- [ ] **#32** 🟠 `[024]` — Construcción de `CocoaMenubarBackend` sin try/except más allá de `ImportError` — un fallo en runtime tumba TODO el cliente — **XS**
- [ ] **#33** 🟠 `[025]` — `voice_client.py` (composition root real del cliente) tiene CERO tests — **M**

### 🟠 Fase 4 — Dependencias, tests y CI (semana 5)

**Objetivo:** que una instalación limpia funcione, y que exista una red de seguridad automatizada mínima.
**Acceptance gate:** `pip install -r requirements.txt && pytest` funciona en una máquina limpia; CI verde en cada PR; cero tests con nombres duplicados.

- [ ] **#34** 🟠 `[026]` — `numpy` y `sounddevice` son dependencias de producción reales pero no están en ningún `requirements*.txt` — **XS**
- [ ] **#35** 🟠 `[027]` — `backends/audio_termux.py` (mitad del objetivo multiplataforma) tiene CERO tests — **S**
- [ ] **#36** 🟠 `[028]` — `backends/audio_capture.py` (base real de captura en Termux) tiene CERO tests — **S**
- [ ] **#37** 🟠 `[029]` — Nombres duplicados que se pisan silenciosamente en tests (confirmado por ruff F811) — **XS**
- [ ] **#38** 🟠 `[030]` — ~57 usos de `MagicMock`/`AsyncMock` en tests, ninguno con `spec=`/`autospec=True` — **M**
- [ ] **#39** 🟠 `[031]` — Fuga de estado global entre tests vía `os.environ` (no vía monkeypatch) — **S**
- [ ] **#40** 🟠 `[032]` — Sin `pytest-timeout` ni timeout global configurado en la suite — **XS**
- [ ] **#41** 🟠 `[033]` — No existe `.github/workflows/` — sin CI configurado en absoluto — **S**

### 🟠 Fase 5 — Instalación y seguridad de despliegue (semana 6)

**Objetivo:** que `install/` sea confiable multiplataforma y no filtre secretos ni datos personales.
**Acceptance gate:** `install.sh` continúa tras el fallo de un paso no crítico; permisos 600 se re-verifican en cada arranque; ningún valor de entorno de producción real queda hardcodeado en código versionado ni en el historial de git; documento de diseño de arquitectura de plataforma (`#102`) mergeado antes de tocar `install/linux/`.

- [ ] **#42** 🟠 `[034]` — Doble stack de instalación (Termux vs macOS) sin punto de entrada único que detecte plataforma — **M**
- [ ] **#43** 🟠 `[035]` — `install.sh` raíz (Termux) usa `source` en vez de subproceso — un `exit 1` mata todo el instalador — **S**
- [ ] **#44** 🟠 `[036]` — Permisos 600 del wizard de config solo se aplican al crear, nunca se re-verifican — evidencia en disco: ya degradados a 644 — **XS**
- [ ] **#45** 🟠 `[037]` — Discrepancia de versión de Python entre `01-homebrew.sh` y `03-venv.sh`/`04-oww.sh` — **XS**
- [ ] **#46** 🟠 `[038]` — LaunchAgents con `KeepAlive=true` sin `ThrottleInterval` explícito ni `SuccessfulExit=false` — **S**
- [ ] **#47** 🟠 `[039]` — README.md desincronizado con los scripts reales de `install/macos/` — se salta el config wizard — **XS**
- [ ] **#48** 🟠 `[040]` — Hostname personal hardcodeado en `install/macos/02-config-wizard.sh` (HEAD committeado) — **S**
- [ ] **#101** 🟠 `[093]` — Eliminar TODOS los valores hardcoded de entorno de producción — estructura agnóstica vía config/env — **M** — *amplía #48; incluye reescribir historial de git*
- [ ] **#102** 🟠 `[094]` — Diseñar arquitectura hexagonal (puertos y adaptadores) para abstracción de plataforma, previo a instaladores Linux/Windows — **L** — *bloquea #42 y el enhancement 9.2*

### 🟡 Fase 6 — Hardening de config, backends y state-machine (semanas 7–8)

**Objetivo:** consolidar validación de configuración, robustez de los backends de audio/display, y comportamiento del bus de eventos ante fallos parciales.
**Acceptance gate:** una config inválida falla al arrancar con un mensaje claro (no en tiempo de ejecución); un fallo de un suscriptor del bus no mata el resto de la sesión.

- [ ] **#49** 🟡 `[041]` — `client/config.py` no valida rangos/tipos — claves desconocidas y valores peligrosos se aceptan en silencio — **M**
- [ ] **#50** 🟡 `[042]` — `config.py` no detecta placeholders sin rellenar (a diferencia del `install.sh` de Termux) — **XS**
- [ ] **#51** 🟡 `[043]` — `_load_env_file` no quita comillas ni comentarios inline de valores `.env` — **XS**
- [ ] **#52** 🟡 `[044]` — `install/macos/04-oww.sh` usa `/tmp/oww-bootstrap-err` fijo (no `mktemp`) — **XS**
- [ ] **#53** 🟡 `[045]` — `install/shared/99-smoke-test.sh`: override de `GW_HOST`/`GW_PORT` por env var es un no-op — **XS**
- [ ] **#54** 🟡 `[046]` — Sin instalador para Linux de escritorio pese a que el cliente Python ya lo soporta — **L**
- [ ] **#55** 🟡 `[047]` — `02-config-wizard.sh` no sanea el device id antes de usarlo en rutas de fichero — **XS**
- [ ] **#56** 🟡 `[048]` — `devices/hab_sito.env`: contraseña real en claro, permisos 644, sin cifrado — **XS**
- [ ] **#57** 🟡 `[049]` — Sin herramienta de escaneo de secretos (gitleaks/pre-commit) — **S**
- [ ] **#58** 🟡 `[050]` — Split-brain de defaults en `DisplayConfig.url` — config inválida silenciosa si se activa el backend http sin url — **XS**
- [ ] **#59** 🟡 `[051]` — `HttpDisplayBackend.update()`: sin cobertura de timeout para la resolución DNS — **S**
- [ ] **#60** 🟡 `[052]` — `platform_detect.py::is_termux()` se basa en un único path hardcodeado, sin fallback — **XS**
- [ ] **#61** 🟡 `[053]` — `ControlServer`: sin límite de conexiones concurrentes, sin rate limiting, sin conformidad HTTP básica — **S**
- [ ] **#62** 🟡 `[054]` — `cancel_event.clear()` al entrar en RECORDING/RESPONDING descarta cancelaciones legítimas recién llegadas — **S**
- [ ] **#63** 🟡 `[055]` — Subscriptores del EventBus sin manejo de excepciones — un fallo de backend mata el task permanentemente — **XS**
- [ ] **#64** 🟡 `[056]` — Backlog sin control en `display_text_update` con backend HTTP lento — **S**
- [ ] **#65** 🟡 `[057]` — Timeouts hardcodeados en `state_machine.py` (`TURN_END_GRACE_S`, timeout RESPONDING) no expuestos en config — **XS**
- [ ] **#66** 🟡 `[058]` — `except Exception: pass` sin ningún logging en `_safe_send_cancel` y `_cleanup` — **XS**

### 🟡 Fase 7 — Hardening de UI/menubar restante (semana 9)

**Objetivo:** cerrar los huecos medios de la capa de menubar y mantener la documentación de diseño sincronizada.
**Acceptance gate:** documentación de diseño del menubar refleja el código real; el contrato `MenubarBackend` es explícito para futuros backends (Linux/Windows).

- [ ] **#67** 🟡 `[059]` — Documentación de diseño del menubar obsoleta respecto al fix de threading ya aplicado — **XS**
- [ ] **#68** 🟡 `[060]` — `NSStatusItem`/`NSMenu` nunca se liberan en `stop()` — leak de recursos Cocoa — **XS**
- [ ] **#69** 🟡 `[061]` — `run_forever()`/`stop()` no forman parte del contrato formal `MenubarBackend` — **S**
- [ ] **#70** 🟡 `[062]` — `NSTimer.invalidate()` invocado desde el hilo equivocado en `stop()` — **XS**
- [ ] **#71** 🟡 `[063]` — Ventana de arranque en la que clics de menú se pierden en silencio — **XS**

### ⚪ Fase 8 — Deuda técnica y limpieza (semanas 10–11)

**Objetivo:** eliminar código muerto, cerrar huecos de test/DX restantes, y pulir inconsistencias menores. Ninguno de estos bloquea producción, pero acumulan fricción de mantenimiento.
**Acceptance gate:** `client/v1/` eliminado o documentado como congelado; typecheck básico funcionando; `ruff` limpio.

- [ ] **#72** ⚪ `[064]` — `client/v1/` (532 líneas, código legacy) confirmado sin uso real — candidato a eliminar — **XS**
- [ ] **#73** ⚪ `[065]` — Sin `requirements-dev.txt` — dependencias de test no declaradas — **XS**
- [ ] **#74** ⚪ `[066]` — Sin typecheck (mypy/pyright) instalado ni configurado — **S**
- [ ] **#75** ⚪ `[067]` — Stubs de `sys.modules` duplicados sin `conftest.py` que los centralice — **S**
- [ ] **#76** ⚪ `[068]` — Tests con timing real ajustado — candidatos a flakiness en CI — **S**
- [ ] **#77** ⚪ `[069]` — De 9 scripts `install/*.sh`, solo una función aislada tiene test, y ni corre vía pytest — **S**
- [ ] **#78** ⚪ `[070]` — `.gitignore` no cubre `.DS_Store` — **XS**
- [ ] **#79** ⚪ `[071]` — `config.example.yaml` tiene una sección `hosts:` que el parser Python ignora silenciosamente — **XS**
- [ ] **#80** ⚪ `[072]` — `config.py`: casts `int()`/`float()` sin manejo de excepción propio en 13+ campos — **S**
- [ ] **#81** ⚪ `[073]` — `registry.py`: sin soporte más allá de macOS/Linux/Termux; sin backend de bandeja para Linux — **L**
- [ ] **#82** ⚪ `[074]` — `OWWClient`: timeout de conexión TCP hardcodeado a 10.0s, sin campo en `OWWConfig` — **XS**
- [ ] **#83** ⚪ `[075]` — `device_id` en el handshake no aparece en el protocolo documentado — **XS**
- [ ] **#84** ⚪ `[076]` — Asimetría de framing entre subida (mic) y bajada (TTS) de audio — **XS**
- [ ] **#85** ⚪ `[077]` — `oww_client.py::send_audio()` hace dos `write()+drain()` separados por chunk — **XS**
- [ ] **#86** ⚪ `[078]` — `AudioCapture.stop()`: tras `kill()` no se vuelve a hacer `wait()` — **XS**
- [ ] **#87** ⚪ `[079]` — `SounddeviceBackend.stop()` cierra streams de forma síncrona/bloqueante en el loop de asyncio — **S**
- [ ] **#88** ⚪ `[080]` — Race de hilos en `_play_leftover`/`_enqueue_carry` sin lock en `audio_sounddevice.py` — **S**
- [ ] **#89** ⚪ `[081]` — `PlaybackEngine.reset()`/`SounddeviceBackend.reset()` no usan el mismo lock que el resto de operaciones — **S**
- [ ] **#90** ⚪ `[082]` — `WyomingBackend` permite construir sin `on_wake_word` y validar en runtime — rama muerta — **XS**
- [ ] **#91** ⚪ `[083]` — `DisplayClient._bus` atributo muerto; guard `isinstance` inalcanzable; sin reacción a `error`/`cancelled` — **XS**
- [ ] **#92** ⚪ `[084]` — Patrón "salida temprana" de async-for sin `contextlib.aclosing` en `_idle`/`_consume_wake` — **XS**
- [ ] **#93** ⚪ `[085]` — `cancel_event.wait()` duplicado/redundante en `_responding` — **S**
- [ ] **#94** ⚪ `[086]` — Inconsistencia docstring/código: `state_changed` dice publicarse "en cada transición" pero solo lo hace para idle — **XS**
- [ ] **#95** ⚪ `[087]` — `PlaybackEngine.play_chunk` recalcula `total_chars`/`full_text` desde cero en cada chunk — O(n²) potencial — **XS**
- [ ] **#96** ⚪ `[088]` — Reinicio de proceso a mitad de turno crea `GatewayClient` nuevo sin handshake de "sesión anterior abortada" — **XS**
- [ ] **#97** ⚪ `[089]` — Inconsistencia de i18n: estado `cancelled` se muestra en inglés/mayúsculas — **XS**
- [ ] **#98** ⚪ `[090]` — `_title_for_state()` siempre devuelve `""` — código muerto; `showAbout_` bloquea el run loop — **XS**
- [ ] **#99** ⚪ `[091]` — Ramas muertas en `MenubarClient._drain_queue()` para 4 de los 5 comandos de UI — **XS**
- [ ] **#100** ⚪ `[092]` — Polling perpetuo de 200ms sobre el executor por defecto compartido — **S**

---

## 🚀 Fase 9+ — Enhancements (backlog)

Enhancements identificados durante la auditoría, alineados con la visión de "monorepo multiplataforma que configure y lance todo lo necesario para usar jota". **No están abiertos como issues** todavía — se priorizan y abren al iniciar esa fase.

### 9.1 — Arquitectura de sesión persistente (✅ dirección confirmada, ver Decisiones tomadas #1)

Se implementa como parte del trabajo de la issue #24 (Fase 2). Las siguientes tareas se abrirán como issues concretas al empezar ese trabajo:

- [ ] **enh** — Migrar de reconexión-por-turno a sesión WebSocket persistente, usando `turn_seq` para descarte de audio antiguo en el barge-in en vez de reconexión completa
- [ ] **enh** — Barge-in real (hablar por encima del asistente) aprovechando `capabilities.barge_in` del protocolo, no solo "repetir la wake word"
- [ ] **enh** — Recibir notificaciones `status` proactivas del gateway durante IDLE (requiere conexión persistente)

### 9.2 — Multiplataforma completo (bloqueado por #102, ver Decisiones tomadas #4)

- [ ] **enh** — `install/linux/*.sh` — instalador completo para Linux de escritorio (systemd user service en vez de LaunchAgent), construido sobre el diseño de #102
- [ ] **enh** — Backend de bandeja para Linux (AppIndicator/GTK) equivalente a `menubar_cocoa.py`, como adaptador del puerto definido en #102
- [ ] **enh** — Backend de bandeja para Windows (`pystray`/Win32), idem
- [ ] **enh** — `install/install.sh` raíz único que detecte plataforma y despache al árbol correcto (Termux/macOS/Linux/Windows) — issue #42, resuelto en el marco de #102
- [ ] **enh** — Clase base compartida para VAD-por-RMS + ring-buffer de preroll entre todos los backends de audio (elimina la duplicación origen de las issues #19/#27) — parte del "puerto universal" de #102

### 9.3 — Observabilidad

- [ ] **enh** — Evento `error`/`degraded` visible en menubar/display cuando OWW o el gateway llevan N reintentos fallidos
- [ ] **enh** — Métricas locales básicas (turnos completados, tasa de timeout, latencia wake-word→respuesta) expuestas en el `ControlServer` (una vez autenticado)

### 9.4 — Seguridad y gestión de secretos

- [ ] **enh** — Migrar `devices/*/config.yaml`/`.env` a Keychain de macOS (y equivalente en Linux/Termux) en vez de ficheros planos
- [ ] **enh** — Rotación de `client_key` sin editar YAML a mano

### 9.5 — DX y CI

- [ ] **enh** — Workflow de CI matriz (macOS + Linux) una vez exista `install/linux/`
- [ ] **enh** — Typecheck real (mypy) sustituyendo los tests de `get_type_hints()`
- [ ] **enh** — `conftest.py` centralizado con fixtures de stubs de `sys.modules`, limpieza de `os.environ`, y timeout global

---

## Decisiones tomadas (2026-07-18)

Las 4 decisiones de diseño identificadas en la auditoría ya están resueltas:

1. **Arquitectura de sesión (#24 / `[016]`) → migrar.** Se pasa de reconexión-por-turno a sesión WebSocket persistente usando `turn_seq` para descartar audio antiguo en el barge-in, en vez de reconexión completa. Habilita el barge-in real (`capabilities.barge_in`) y las notificaciones `status` proactivas durante IDLE. Alcance detallado en enhancement 9.1; se abrirán issues concretas al empezar el trabajo de Fase 2.

2. **Reescritura de historial de git + estructura agnóstica al entorno (#48 / `[040]`, ampliado en #101) → sí, en ambos frentes.** No solo se elimina el hostname hardcodeado puntual: se audita el repo completo en busca de cualquier URL/IP/hostname de producción real hardcodeado, se migra todo a config/env (ningún fichero versionado debe requerir edición para apuntar a otro despliegue), y se reescribe el historial de git (`filter-repo`/BFG) para limpiar lo ya comiteado — el repo ya es público.

3. **Autenticación del ControlServer (#10 / `[002]`) → ambos mecanismos.** Token compartido en fichero con permisos 600 **y** validación de un header custom que un navegador no pueda fijar (mitiga el vector de ataque desde cualquier pestaña, issue #10) — defensa en profundidad, no uno u otro.

4. **Arquitectura de plataforma previa al instalador Linux (#54 / `[046]`, resuelto en #102) → sí, arquitectura hexagonal explícita primero.** Antes de construir `install/linux/` o cualquier adaptador nuevo, se diseña una arquitectura de puertos y adaptadores donde: los adaptadores por plataforma (audio, menubar, instalación) son lo más pequeños y mantenibles posible; la lógica universal (VAD, preroll, framing de protocolo, selección de plataforma) está centralizada en un solo sitio, no duplicada (síntomas ya vistos en #19/#27/#42); y se prioriza velocidad/eficiencia — "hexagonal" aquí es disciplina de separación puerto/adaptador, no un framework pesado. Ver issue #102 para el entregable concreto (documento de diseño).

---

## Risk register

| Riesgo | Prob. | Impacto | Mitigación |
|---|---|---|---|
| Fix de la cola de audio compartida (#9) introduce una regresión en la detección de wake word durante RESPONDING | Media | Alto | Test de integración nuevo que ejercite ambos consumidores reales antes/después del fix (issue #33 lo habilita) |
| Migrar a sesión persistente (#24) rompe el barge-in "repetir wake word" que ya funciona | Media | Alto | Feature flag de arquitectura de sesión; mantener el camino por-turno como fallback hasta validar en campo |
| Reescribir el historial de git (#48) rompe clones/forks existentes | Baja | Medio | Coordinar con cualquier colaborador antes de forzar push; documentar el hash antiguo en el PR |
| Autenticación nueva del ControlServer (#10) rompe integraciones locales existentes (si las hay) | Baja | Bajo | Buscar cualquier consumidor real del endpoint antes de romper compatibilidad |
| Unificar `is_silence()`/VAD (#27) cambia el comportamiento de corte de silencio en producción | Media | Medio | Mantener el guard de array vacío como comportamiento canónico; test de regresión con frames reales grabados |
| Sin CI durante las Fases 1-4, los fixes se validan solo manualmente | Alta | Medio | Priorizar issue #41 (CI básico) lo antes posible dentro de la Fase 4, o adelantarla si se detectan regresiones |

---

## Acceptance gates por milestone

| Milestone | Criterio |
|---|---|
| **Fase 1 done** | 12 🔴 cerrados, suite de tests verde, 10 turnos manuales consecutivos sin degradación de audio, ControlServer rechaza sin token |
| **Fase 2 done** | Simulación de red degradada sin cuelgues, `is_silence()` unificado, cero secretos visibles en logs DEBUG |
| **Fase 3 done** | Apagar desde el menú no bloquea >200ms, fallo del menubar no impide arrancar el resto, `voice_client.py` con test de integración |
| **Fase 4 done** | Instalación limpia funciona, CI verde en cada PR, cero nombres de test duplicados |
| **Fase 5 done** | `install.sh` continúa tras fallo no crítico, permisos 600 re-verificados en arranque, historial de git limpio de hostname personal |
| **Fase 6 done** | Config inválida falla al arrancar con mensaje claro, fallo de un suscriptor del bus no mata la sesión |
| **Fase 7 done** | Documentación de menubar sincronizada con el código, contrato `MenubarBackend` explícito |
| **Fase 8 done** | `client/v1/` eliminado o documentado como congelado, typecheck básico en marcha, `ruff` limpio |

---

## Cómo actualizar este documento

1. **Al cerrar una issue** → marca su casilla con `[x]` y enlaza el PR que la cierra.
2. **Al abrir una issue nueva** → añádela a la fase correspondiente con su número `#NNN`.
3. **Si una issue cambia de fase** → muévela (no la dupliques).
4. **Al cerrar una fase entera** → actualiza el acceptance gate correspondiente y el estado en el TL;DR.
5. **Si añades un enhancement** → documéntalo en "Fase 9+" con `enh` como prefijo.
6. **Si descubres un nuevo bug** → crea una issue primero (con label `audit:<fecha>` si viene de una auditoría); después añádela aquí.

Este documento se actualiza en el mismo PR que cierra la issue, o en un PR dedicado. Cadencia recomendada: al cierre de cada fase.

---

## Referencias

- [README.md](../README.md) — documentación de instalación y uso
- [config.example.yaml](../config.example.yaml) — plantilla de configuración
- [install/](../install/) — instaladores por plataforma (macOS, Termux)
- Issues de la auditoría: https://github.com/Jota-project/jota-voice/issues?q=label:audit:2026-07-14
- [jota-gateway/docs/ROADMAP.md](https://github.com/Jota-project/jota-gateway/blob/main/docs/ROADMAP.md) — roadmap del repo hermano (mismo formato)
