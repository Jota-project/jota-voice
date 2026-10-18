# jota-voice

Cliente de voz del ecosistema [Jota](https://github.com/Jota-project), un asistente de voz distribuido y modular.

Escucha la wake word en local (Wyoming OpenWakeWord), graba hasta que detecta silencio y hace streaming del audio a [`jota-gateway`](https://github.com/Jota-project/jota-gateway) por una sesión WebSocket persistente. Reproduce la respuesta TTS a medida que llega. Corre en macOS (con icono en la barra de menús) y en Android/Termux (junto al kiosk [`jota-display`](https://github.com/SitoSt/jota-display)). En Linux el cliente funciona, pero aún no tiene instalador (#54).

Ver la [arquitectura completa del ecosistema](https://github.com/Jota-project/.github/blob/main/ARCHITECTURE.md).

## Instalación

Un solo punto de entrada, que detecta la plataforma y lanza el instalador que toca:

```bash
git clone https://github.com/Jota-project/jota-voice.git && cd jota-voice
sh install.sh            # sh install.sh --detect solo dice qué plataforma ve
```

| Plataforma | Instalador | Servicio |
|---|---|---|
| macOS | `install/macos/install.sh` | LaunchAgents `com.jota.voice` (cliente), `com.jota-voice.wyoming-oww` (wake word) y `com.jota-voice.logrotate` |
| Termux (Android) | `install/install-termux.sh` | supervisord + Termux:Boot |
| Linux | todavía no (#54); `install.sh` explica qué montar a mano | — |

Todos los instaladores son idempotentes: se pueden relanzar para reparar o actualizar. Exigen **Python ≥ 3.11**.

### macOS

`install/macos/install.sh` encadena estos pasos (cada uno se puede ejecutar suelto con `sh install/macos/<paso>`):

1. `01-homebrew.sh`: Homebrew, `python@3.12`, PortAudio y sox.
2. `02-config-wizard.sh`: crea `devices/<id>/config.yaml` si no existe y pregunta la URL del gateway y el `client_key`. No pregunta lo que ya esté en `GATEWAY_URL` / `GATEWAY_CLIENT_KEY`.
3. `03-venv.sh`: venv del cliente en `~/venvs/jota-voice`.
4. `04-oww.sh`: Wyoming OpenWakeWord nativo (venv + LaunchAgent) en el puerto 10401. `04-oww.sh uninstall` lo quita.
5. `06-configs.sh`: enlaza `~/Library/Application Support/jota-voice/config.yaml` → `devices/<id>/config.yaml`.
6. `07-launchd.sh`: LaunchAgent del cliente y rotación de logs.
7. `install/shared/99-smoke-test.sh`: comprobación final (servicio, OWW, config, gateway, log).

Variables de entorno opcionales:

| Variable | Defecto | Para qué |
|---|---|---|
| `DEVICE_ID` | el único `devices/*/config.yaml`, o el hostname | id del dispositivo (`[A-Za-z0-9_-]`) |
| `GATEWAY_URL`, `GATEWAY_CLIENT_KEY` | se preguntan | para que el wizard no pregunte |
| `JOTA_PYTHON` | `python@3.12` de Homebrew | intérprete de los venvs (≥ 3.11) |
| `JOTA_KEEP_ALIVE` | `on-failure` | `on-failure` relanza solo tras un fallo; `always`; `never` |
| `JOTA_THROTTLE_INTERVAL`, `JOTA_EXIT_TIMEOUT` | `30`, `30` | segundos entre relanzamientos / margen de apagado antes del SIGKILL |
| `JOTA_LOG_MAX_KB`, `JOTA_LOG_KEEP` | `10240`, `3` | rotación horaria de logs |
| `OWW_THRESHOLD` | `0.40` | umbral de detección de la wake word |

Por defecto launchd solo relanza el cliente si sale con error, esperando 30 s entre intentos. "Apagar servicio" en el menú lo para hasta el próximo inicio de sesión o hasta `launchctl kickstart gui/$(id -u)/com.jota.voice`.

Desplegar cambios del repo al Mac: `bash deploy.sh macbook` (rsync a `$REPO_LOCAL`, por defecto `~/Work/jota-voice`, y reinicio del servicio).

### Termux (Android)

En el teléfono, la primera vez:

```bash
pkg install git && git clone https://github.com/Jota-project/jota-voice.git ~/jota-voice && cd ~/jota-voice
mkdir -p devices/<id> && cp config.example.yaml devices/<id>/config.yaml && nano devices/<id>/config.yaml
sh install.sh
```

El instalador valida que el config no tenga placeholders y ejecuta cada paso de `install/` en un subproceso. Esos pasos instalan paquetes, hosts, venv, OWW, supervisord, configs, Termux:Boot y jota-display, y al final pasa el smoke test. Si falla un paso no crítico, sigue con el resto y al final lista los que fallaron. Si falla `01-packages.sh`, aborta.

Desde el Mac, con la conexión SSH del teléfono en `devices/<nombre>.env` (plantilla: `devices/example.env`):

```bash
bash deploy.sh phone <nombre>      # rsync + reinicio de jota-voice
./jota-voice <nombre> update       # además reaplica configs/boot y reinicia todos los servicios
./jota-voice <nombre> logs         # status | logs | restart | setup | adb-port
```

Para la conexión SSH conviene una clave (`ssh-copy-id -p 8022 …`) y dejar `PHONE_PASS` vacío. Si se usa contraseña, se le pasa a `sshpass` por entorno, nunca en la línea de órdenes.

## Configuración

Cada dispositivo tiene su `devices/<id>/config.yaml`, que **nunca se versiona** porque lleva el `client_key`. La plantilla es [`config.example.yaml`](config.example.yaml). El cliente valida el config al arrancar y rechaza `RELLENAR`, `YOUR_…` y `CHANGE-ME` en los campos de secretos y endpoints. Los instaladores dejan `devices/*/config.yaml`, `devices/*/.env` y `devices/*.env` en `600` en cada ejecución.

- **`gateway`**: `url`, o `host`/`port`/`path`/`scheme`; además `client_key` y `connect_timeout_s`. Sesión persistente:
  - `persistent_session` (por defecto `true`);
  - `reconnect_backoff_s`: esperas entre reintentos; el último valor se repite;
  - `keepalive_interval_s`: keepalive de aplicación en reposo, porque el gateway cierra tras 300 s sin mensajes; `0` lo desactiva;
  - `ping_interval_s` / `ping_timeout_s`: ping de protocolo para detectar una red o un gateway muertos.

  Un rechazo 1008 (`client_key` inválida) no se reintenta solo.
- **`oww`**: servidor Wyoming (`127.0.0.1:10401`), `wake_words`, `reconnect_backoff_s` y `debounce_s`.
- **`audio`**: `backend` (`null` elige por plataforma), VAD por RMS (`vad_rms_threshold`, `silence_timeout_s`), `preroll_seconds` y `recording_timeout_s`.
- **`display`**: ver [Displays](#displays). La sección `control:` está obsoleta: el ControlServer ya no existe y el cliente la ignora con un aviso.
- **`menubar`** (macOS): `enabled`, `refresh_hz`, `log_path`, `config_path`. `JOTA_DISABLE_MENUBAR=1` lo desactiva.

Las cabeceras de Cloudflare Access, si el gateway está detrás, van en `devices/<id>/.env` (`CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`).

## Turno de voz y señales al gateway

`IDLE` (escuchando la wake word) → `RECORDING` (audio en streaming) → `RESPONDING` (TTS) → `IDLE`.

- **`{"type":"end"}`**: el cliente la envía al detectar silencio (VAD) o al agotar `recording_timeout_s`. El transcriptor cierra la transcripción aunque su VAD no haya disparado.
- **`{"type":"cancel"}`**: cancela el turno en curso sin abrir otro. La envía el comando `cancel` de un display por WebSocket, y también una wake word dicha durante la respuesta (barge-in: corta el TTS y empieza un turno nuevo).

## Barra de menús (macOS)

El icono refleja el estado:

| Estado | Icono (SF Symbol) | Significado |
|---|---|---|
| `idle` | `mic` | Esperando wake word |
| `listening` | `ear` | Grabando |
| `thinking` | `brain` | Esperando respuesta del gateway |
| `speaking` | `speaker.wave.2` | Reproduciendo TTS |
| `error` | `exclamationmark.triangle` | El último turno terminó en error |
| `cancelled` | `xmark.circle` | Turno cancelado |

En reposo también avisa de problemas fuera del turno:

- `mic.slash` si algo falla pero se reintenta solo: gateway reconectando, wake word no disponible o un servicio caído.
- `exclamationmark.octagon` si hace falta intervenir, por ejemplo con un `client_key` inválido.

Bajo la cabecera del menú aparece una **línea de aviso** solo cuando hay algo que contar: "Gateway: reconectando…", "Transcripción degradada", "Wake word no disponible", "Gateway: client_key inválida" (en rojo)… Pulsarla abre los logs. Se limpia sola cuando el gateway vuelve (`restored`/`connected`) o cuando un turno sale bien.

El menú también tiene: **Servicio** (Pausar/Reanudar escucha, Apagar servicio), Abrir logs, Abrir configuración, Acerca de y Salir.

Logs: `~/Library/Logs/jota-voice/`. El logging del cliente va a `stderr.log`. Para ver el estado del servicio: `launchctl print gui/$(id -u)/com.jota.voice`.

## Displays

El cliente empuja su estado a displays externos según `display.backend`:

| Backend | Cuándo | Qué hace |
|---|---|---|
| `ws` | por defecto en macOS y Linux | Servidor WebSocket en `127.0.0.1:8767` con token (`~/.jota-voice/display_token`, rotado en cada arranque, `600`): `sync`, texto por chunk, tool calls, errores, avisos de salud y comando `cancel` |
| `http` | por defecto en Termux, y siempre que haya `display.url` | `POST /state` al kiosk de jota-display en `http://127.0.0.1:8766`; el kiosk aún no habla `ws` |
| `null` | — | Sin display |

El protocolo completo está en **[`docs/display-protocol.md`](docs/display-protocol.md)**, la fuente de verdad para cualquier display.

## Seguridad

- **Cero valores del despliegue real en el código versionado**: nada de hostnames, IPs, dominios ni usuarios. Todo sale de `devices/` (ignorado por git) o de variables de entorno. Ningún fichero versionado debe necesitar cambios para apuntar a otro despliegue.
- Escaneo de secretos con [gitleaks](https://github.com/gitleaks/gitleaks) (`.gitleaks.toml`):
  - en local, `pip install pre-commit && pre-commit install`;
  - en CI, el job `secrets` escanea los commits de cada push o PR.

## Desarrollo

```bash
cd client && python -m pytest -q                               # tests de Python
for t in install/shared/tests/test_*.sh; do sh "$t"; done      # tests de los instaladores
```

```text
client/
  app/          voice_client.py (entry point y composición), playback, display_client
  domain/       máquina de estados, event bus, sesión del gateway
  core/         lógica universal: detección de plataforma, VAD, preroll, framing, asyncio
  backends/     adaptadores: audio (sounddevice / Termux), wake word (Wyoming), display (ws/http/null), gateway, registry
  platforms/    código solo de una plataforma (termux/)
  ui/           menubar (Cocoa en macOS, null en el resto)
install/        instaladores: macos/, pasos de Termux, shared/ (smoke test y tests)
lib/            helpers de shell: plataforma, permisos, Python, id de dispositivo, YAML
boot/, kiosk/   arranque en Termux (supervisord, Termux:Boot) y hooks del kiosk
devices/        configs y credenciales por dispositivo (ignorado por git salvo example.env)
install.sh      punto de entrada de instalación
deploy.sh       deploy a phone <nombre> | macbook
jota-voice      CLI de gestión de teléfonos desde el Mac
```

Documentación técnica adicional (parte legacy): [`install/docs/`](install/docs/) (spec, arquitectura, kiosk, nginx, bootstrap del teléfono, parches).
