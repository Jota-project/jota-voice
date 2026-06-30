# jota-voice Phone Bootstrap & Management — Design Spec

**Date:** 2026-06-16  
**Status:** Approved

---

## Objetivo

Sistema completo para configurar, arrancar y monitorizar todos los servicios del teléfono Android (Termux/LineageOS) que componen el stack de jota-voice. Debe funcionar desde suelo limpio (LineageOS recién instalado) y soportar múltiples dispositivos en el futuro.

---

## Alcance

**Dentro del scope:**
- Teléfono Android/Termux: OWW, jota-voice, jota-display, PulseAudio/sles-source, FullyKiosk
- CLI Mac-side para gestión y despliegue
- Bootstrap desde USB (ADB) para setup inicial
- Soporte multi-dispositivo desde el primer día

**Fuera del scope:**
- Servicios de green-house (jota-gateway, transcriber, etc.)
- Mac-side (adb_screen.py, kiosk_server.py)

---

## Arquitectura general

Tres capas con responsabilidades claras:

```
MAC                                      TELÉFONO
─────────────────────────────────────    ──────────────────────────────────
bootstrap (USB/ADB, una vez)  ──────►   instala Termux + APKs + SSH

jota-voice CLI (SSH continuo) ──────►   install.sh  (idempotente)
  jota-voice [device] status  ──────►   supervisorctl status
  jota-voice [device] update  ──────►   rsync + reload
  jota-voice [device] logs    ──────►   tail logs

                                         supervisord (runtime)
                                           ├── oww
                                           ├── jota-display
                                           └── jota-voice

                                         boot/hook.sh (pre-condiciones)
                                           PulseAudio → sles-source
                                           → supervisord → FullyKiosk
```

---

## Ficheros nuevos / modificados

```
jota-voice/
├── jota-voice                      # CLI ejecutable (Mac-side, nuevo)
├── bootstrap.sh                    # setup USB inicial (Mac-side, nuevo)
├── install.sh                      # refactorizado (corre en teléfono)
├── jota-env.example.sh             # plantilla de entorno del teléfono (nuevo)
├── devices/
│   ├── .gitkeep
│   └── example.env                 # plantilla de dispositivo (nuevo, versionado)
├── boot/
│   ├── hook.sh                     # fuente de verdad del boot hook (nuevo)
│   ├── supervisord.conf.tpl        # plantilla supervisord (nuevo)
│   └── start.sh                    # ELIMINADO
├── jota-watchdog.sh                # ELIMINADO (reemplazado por supervisord)
└── docs/
    └── fullyKiosk-setup.md         # instrucciones manuales (nuevo)
```

`.gitignore` añade `devices/*.env` (excepto `example.env`).

---

## Multi-dispositivo

### devices/example.env

```sh
# Nombre del dispositivo (para logs y mensajes)
DEVICE_NAME="salon"

# Conexión SSH
PHONE_HOST="192.168.1.129"
PHONE_PORT="8022"
PHONE_PASS="CAMBIAR"

# Directorio del repo en el teléfono
PHONE_DIR="/data/data/com.termux/files/home/jota-voice"
```

### Descubrimiento automático

El CLI carga todos los ficheros `devices/*.env` (excluyendo `example.env`). Si solo hay uno, es el dispositivo por defecto. Si hay varios, el primer argumento del comando es el nombre del dispositivo.

---

## CLI: jota-voice

Fichero ejecutable en la raíz del repo (`chmod +x`), escrito en bash.

### Sintaxis

```bash
# Un dispositivo → nombre opcional
jota-voice status
jota-voice update

# Varios dispositivos → nombre obligatorio
jota-voice salon status
jota-voice cocina logs jota-voice

# Sin dispositivo
jota-voice bootstrap             # setup USB vía ADB
jota-voice devices               # lista dispositivos configurados
jota-voice --all status          # revisa todos
```

### Comandos

| Comando | Acción |
|---|---|
| `bootstrap` | Setup inicial desde USB vía ADB |
| `devices` | Lista dispositivos en `devices/*.env` |
| `[device] setup` | Ejecuta `install.sh` en el teléfono vía SSH |
| `[device] status` | `supervisorctl status` vía SSH |
| `[device] logs [svc]` | `tail -f ~/jota-voice.log` (u oww, jota-display) |
| `[device] restart [svc]` | `supervisorctl restart <svc>` vía SSH |
| `[device] update` | rsync repo + `supervisorctl restart jota-voice` (solo jota-voice; OWW y jota-display no cambian con deploys normales) |
| `[device] adb-port` | Lee `~/adb_port.txt` del teléfono |
| `--all status` | Itera todos los dispositivos |

### Implementación

Shell script (~200 líneas). Usa `sshpass` para SSH sin interacción (igual que el `deploy.sh` actual). Lee `devices/<name>.env` para los datos del dispositivo.

`--all status`: itera todos los dispositivos; si uno es inalcanzable, muestra `[device] UNREACHABLE` en rojo y continúa con los demás. El exit code es 1 si algún dispositivo falló.

---

## bootstrap.sh (Mac, USB, una vez)

Precondición: LineageOS instalado, developer options activos, USB debugging ON, cable conectado.

```
1.  adb devices → verificar un dispositivo conectado (error si hay 0 o >1)
2.  Descargar APKs si no existen en ~/.jota-voice/apks/:
      - Termux (F-Droid build, URL fija en script)
      - Termux:Boot
      - Termux:API
      - FullyKiosk Browser (fullyKiosk.com)
3.  adb install Termux.apk
    adb install TermuxBoot.apk
    adb install TermuxAPI.apk
    adb install FullyKiosk.apk
4.  adb shell am start -n com.termux/.app.TermuxActivity
    sleep 5  # esperar inicialización inicial de Termux
5.  Preparar script de setup mínimo en Mac → push vía ADB:
      adb push /tmp/termux_init.sh /sdcard/termux_init.sh
      (el script instala openssh, configura password, arranca sshd)
6.  Ejecutar script en Termux vía input ADB:
      adb shell input text "bash /sdcard/termux_init.sh"
      adb shell input keyevent 66  # Enter
      sleep 30  # esperar instalación de paquetes
7.  Leer IP del dispositivo:
      IP=$(adb shell ip route | awk '/wlan0/ {print $9}' | head -1)
8.  Crear devices/<nombre>.env con IP detectada
    (pide nombre de dispositivo al usuario interactivamente)
9.  Pedir interactivamente los valores de jota-env.sh (DNS_SERVER, etc.)
    scp jota-env.sh → teléfono vía SSH (puerto 8022)
10. scp config.yaml → teléfono vía SSH
11. SSH → git clone jota-voice repo → ejecutar install.sh
12. Imprimir resumen: ✓ APKs instalados, ✓ SSH activo, ✓ supervisord corriendo
```

---

## install.sh (teléfono, idempotente)

Cada paso comprueba si ya está hecho antes de actuar.

```
Step 0: Validar configuración
  ├── ¿Existe ~/jota-env.sh?         Si no → copia example, EXIT + instrucciones
  ├── ¿Existe config.yaml?           Si no → copia example, EXIT + instrucciones
  └── ¿Campos "CAMBIAR" presentes?   Lista cuáles, EXIT

Step 1: Paquetes Termux
  pkg install python pulseaudio termux-api git openssh \
              termux-tools ffmpeg
  (skip si ya instalado)

Step 2: venv jota-voice
  [ -d .venv ] → pip install -r requirements.txt --upgrade
  [ ! -d .venv ] → python -m venv .venv && pip install -r requirements.txt

Step 3: venv OWW + modelo ok_nabu
  [ -d ~/oww-venv ] && modelo existe → skip
  En caso contrario → crear venv, pip install, descargar modelo

Step 4: pip install supervisor
  which supervisord → skip si existe

Step 5: Generar ~/supervisord.conf
  envsubst < boot/supervisord.conf.tpl > ~/supervisord.conf
  (siempre regenera: es código del repo, no config del usuario)

Step 6: Instalar boot hooks
  cp boot/hook.sh ~/.termux/boot/jota-voice
  chmod +x ~/.termux/boot/jota-voice
  (siempre copia: es código del repo)

Step 7: Smoke test
  supervisord -c ~/supervisord.conf
  sleep 10
  supervisorctl -c ~/supervisord.conf status
  → imprime ✓/✗ por servicio
```

---

## supervisord.conf.tpl

```ini
[supervisord]
logfile=%(ENV_HOME)s/supervisord.log
pidfile=%(ENV_HOME)s/supervisord.pid
nodaemon=false

[unix_http_server]
file=%(ENV_HOME)s/supervisor.sock

[supervisorctl]
serverurl=unix:///%(ENV_HOME)s/supervisor.sock

[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface

[program:oww]
command=%(ENV_HOME)s/oww-venv/bin/python3 -m wyoming_openwakeword
    --uri tcp://0.0.0.0:10401
    --preload-model ok_nabu
    --threshold 0.1
directory=%(ENV_HOME)s
stdout_logfile=%(ENV_HOME)s/oww.log
stderr_logfile=%(ENV_HOME)s/oww.log
autorestart=true
startretries=10
startsecs=5
priority=10

[program:jota-display]
command=python3 %(ENV_HOME)s/jota-display/server/server.py
directory=%(ENV_HOME)s/jota-display
stdout_logfile=%(ENV_HOME)s/jota-display.log
stderr_logfile=%(ENV_HOME)s/jota-display.log
autorestart=true
startretries=10
startsecs=3
priority=20

[program:jota-voice]
command=%(ENV_HOME)s/jota-voice/.venv/bin/python3
    %(ENV_HOME)s/jota-voice/client/voice_client.py
    %(ENV_HOME)s/jota-voice/config.yaml
directory=%(ENV_HOME)s/jota-voice
stdout_logfile=%(ENV_HOME)s/jota-voice.log
stderr_logfile=%(ENV_HOME)s/jota-voice.log
autorestart=true
startretries=10
startsecs=5
priority=30
```

---

## boot/hook.sh

Pre-condiciones del sistema que supervisord no puede gestionar (Android-specific):

```sh
#!/data/data/com.termux/files/usr/bin/sh
# jota-voice boot hook v5

LOG=$HOME/boot.log
echo "=== boot $(date) ===" >> "$LOG"

# 1. Entorno
. "$HOME/jota-env.sh"

# 2. sshd + DNS
sshd && echo "sshd OK" >> "$LOG" || echo "sshd ERROR" >> "$LOG"
{ echo "nameserver $DNS_SERVER"; echo "nameserver 8.8.8.8"; } \
    > /data/data/com.termux/files/usr/etc/resolv.conf

# 3. PulseAudio
pulseaudio --start 2>/dev/null || true
for _i in $(seq 1 30); do
    pactl info >/dev/null 2>&1 && break; sleep 1
done

# 4. Mic warm-up → desbloquea OpenSL ES → sles-source
#    Android bloquea OpenSL ES hasta que MediaRecorder es usado al menos una vez
termux-microphone-record -d 1 2>/dev/null || true
sleep 3
termux-microphone-record -q 2>/dev/null || true
sleep 1
pactl load-module module-sles-source 2>/dev/null \
    && echo "sles-source OK" >> "$LOG" \
    || echo "sles-source WARN — watchdog reintentará" >> "$LOG"

# 5. Exponer puerto ADB para kiosk_server
getprop service.adb.tls.port > "$HOME/adb_port.txt" 2>/dev/null

# 6. supervisord
supervisord -c "$HOME/supervisord.conf" \
    && echo "supervisord OK" >> "$LOG" \
    || echo "supervisord ERROR" >> "$LOG"

# 7. Esperar jota-display → abrir FullyKiosk
_deadline=$(( $(date +%s) + 120 ))
while ! bash -c 'exec 3<>/dev/tcp/127.0.0.1/8766 && exec 3>&-' 2>/dev/null; do
    [ $(date +%s) -ge $_deadline ] && break
    sleep 2
done
am start -n de.ozerov.fully/.MainActivity 2>/dev/null
echo "boot COMPLETADO: $(date)" >> "$LOG"
```

---

## jota-env.example.sh

```sh
# Entorno del sistema para el teléfono
# Copia este fichero a ~/jota-env.sh y rellena los valores

# DNS del router local
DNS_SERVER="CAMBIAR"

# (Añadir aquí otras variables de entorno específicas del sistema)
```

---

## docs/fullyKiosk-setup.md

Instrucciones manuales (no automatizable) para configurar FullyKiosk:
1. Abrir FullyKiosk tras la instalación
2. Start URL: `http://localhost:8766`
3. Microphone access: **DISABLED** (crítico — si está activo bloquea sles-source)
4. Autostart on boot: **DISABLED** (lo gestiona el boot hook)
5. Kiosk mode: enabled
6. Screen timeout: managed externally (kiosk_server.py vía ADB)

---

## Qué se elimina

| Fichero | Motivo |
|---|---|
| `boot/start.sh` | Reemplazado por supervisord + hook.sh |
| `jota-watchdog.sh` | Reemplazado por supervisord |
| `~/.termux/boot/jota-voice` (v4) | Generado por install.sh desde boot/hook.sh |
| `deploy.sh` | Reemplazado por `jota-voice [device] update` |

---

## APKs necesarios para bootstrap

| App | Fuente | Nota |
|---|---|---|
| Termux | F-Droid | NO usar Google Play (versión antigua) |
| Termux:Boot | F-Droid | mismo repo que Termux |
| Termux:API | F-Droid | necesario para termux-microphone-record |
| FullyKiosk Browser | fullyKiosk.com | APK directo, no en stores |

`bootstrap.sh` los descarga automáticamente a `~/.jota-voice/apks/` (Mac-local cache).

---

## Consideraciones de seguridad

- `devices/*.env` está en `.gitignore` — nunca se versiona
- `config.yaml` está en `.gitignore` — nunca se versiona
- `~/jota-env.sh` vive solo en el teléfono
- Las contraseñas SSH no se loguean en ningún fichero de log
