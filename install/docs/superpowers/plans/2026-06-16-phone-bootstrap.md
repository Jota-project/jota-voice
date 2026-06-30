# Phone Bootstrap & Management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar el sistema completo de bootstrap, gestión de servicios y CLI multi-dispositivo para el teléfono Android (Termux/LineageOS) de jota-voice.

**Architecture:** Mac-side CLI (`jota-voice`) coordina setup y operaciones vía SSH. El teléfono corre supervisord gestionando OWW, jota-display y jota-voice como servicios con autorestart. Un boot hook (`~/.termux/boot/jota-voice`) maneja las pre-condiciones Android-específicas (PulseAudio, mic warm-up, sles-source) antes de lanzar supervisord. `install.sh` es idempotente: detecta qué pasos ya están hechos y los saltea.

**Tech Stack:** Bash, supervisord (pip install supervisor), SSH/sshpass/rsync, ADB (solo para bootstrap USB), Termux:Boot

---

### Task 1: Scaffold — .gitignore, devices/, jota-env.example.sh

**Files:**
- Create: `.gitignore`
- Create: `devices/example.env`
- Create: `jota-env.example.sh`

- [ ] **Step 1: Crear .gitignore**

```bash
cat > /path/to/jota-voice/.gitignore << 'EOF'
# Config de usuario (no versionar)
config.yaml
.env.local
jota-env.sh

# Entornos virtuales
.venv/
__pycache__/
*.pyc
*.pyo

# Dispositivos (solo example.env se versiona)
devices/*.env
!devices/example.env

# Cache de APKs del bootstrap (local del Mac)
~/.jota-voice/

# pytest
.pytest_cache/
EOF
```

- [ ] **Step 2: Crear devices/ con .gitkeep y example.env**

```bash
mkdir -p devices
touch devices/.gitkeep
cat > devices/example.env << 'EOF'
# Nombre del dispositivo (para logs y mensajes)
DEVICE_NAME="salon"

# Conexión SSH
PHONE_HOST="192.168.1.129"
PHONE_PORT="8022"
PHONE_PASS="CAMBIAR"

# Directorio del repo en el teléfono
PHONE_DIR="/data/data/com.termux/files/home/jota-voice"
EOF
```

- [ ] **Step 3: Crear jota-env.example.sh**

```bash
cat > jota-env.example.sh << 'EOF'
# Entorno del sistema para el teléfono.
# Copia este fichero a ~/jota-env.sh y rellena los valores.
# NO subas jota-env.sh al repo — vive solo en el teléfono.

# DNS del router local (para que Termux resuelva nombres tras el boot)
DNS_SERVER="CAMBIAR"

# (Añadir aquí otras variables de entorno específicas del sistema)
EOF
```

- [ ] **Step 4: Verificar**

```bash
# En la raíz del repo:
cat .gitignore
cat devices/example.env
cat jota-env.example.sh
```

Expected: los tres ficheros existen con el contenido correcto.

- [ ] **Step 5: Commit**

```bash
git add .gitignore devices/.gitkeep devices/example.env jota-env.example.sh
git commit -m "feat: scaffold devices/, .gitignore y jota-env.example.sh"
```

---

### Task 2: boot/supervisord.conf.tpl

**Files:**
- Create: `boot/supervisord.conf.tpl`

- [ ] **Step 1: Crear boot/supervisord.conf.tpl**

```ini
# boot/supervisord.conf.tpl
# supervisord usa %(ENV_HOME)s para expandir $HOME en tiempo de ejecución.
# install.sh copia este fichero a ~/supervisord.conf.

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

- [ ] **Step 2: Verificar sintaxis básica**

```bash
grep -c "\[program:" boot/supervisord.conf.tpl
```

Expected: `3`

- [ ] **Step 3: Commit**

```bash
git add boot/supervisord.conf.tpl
git commit -m "feat: añadir boot/supervisord.conf.tpl para gestión de servicios"
```

---

### Task 3: boot/hook.sh — Fuente de verdad del boot hook

**Files:**
- Create: `boot/hook.sh` (reemplaza boot/start.sh)
- Delete: `boot/start.sh`

- [ ] **Step 1: Crear boot/hook.sh**

```sh
#!/data/data/com.termux/files/usr/bin/sh
# jota-voice boot hook v5
# Instalado en ~/.termux/boot/jota-voice por install.sh.
# Gestiona las pre-condiciones Android antes de lanzar supervisord.

LOG=$HOME/boot.log
echo "=== boot $(date) ===" >> "$LOG"

# 1. Cargar entorno del sistema
. "$HOME/jota-env.sh"

# 2. sshd + DNS
sshd && echo "sshd OK" >> "$LOG" || echo "sshd ERROR" >> "$LOG"
{ echo "nameserver $DNS_SERVER"; echo "nameserver 8.8.8.8"; } \
    > /data/data/com.termux/files/usr/etc/resolv.conf

# 3. PulseAudio — arrancar y esperar hasta que esté listo (máx 30s)
pulseaudio --start 2>/dev/null || true
for _i in $(seq 1 30); do
    pactl info >/dev/null 2>&1 && break
    sleep 1
done

# 4. Mic warm-up: Android bloquea OpenSL ES hasta que MediaRecorder se usa al menos una vez.
#    termux-microphone-record usa MediaRecorder y desbloquea sles-source para procesos en background.
termux-microphone-record -d 1 2>/dev/null || true
sleep 3
termux-microphone-record -q 2>/dev/null || true
sleep 1
pactl load-module module-sles-source 2>/dev/null \
    && echo "sles-source OK" >> "$LOG" \
    || echo "sles-source WARN — supervisord/oww reintentará" >> "$LOG"

# 5. Guardar puerto ADB inalámbrico (cambia en cada reboot)
getprop service.adb.tls.port > "$HOME/adb_port.txt" 2>/dev/null

# 6. Lanzar supervisord (gestiona oww, jota-display, jota-voice)
supervisord -c "$HOME/supervisord.conf" \
    && echo "supervisord OK" >> "$LOG" \
    || echo "supervisord ERROR" >> "$LOG"

# 7. Esperar jota-display (puerto 8766) → abrir FullyKiosk
_deadline=$(( $(date +%s) + 120 ))
while ! (exec 3<>/dev/tcp/127.0.0.1/8766) 2>/dev/null; do
    [ $(date +%s) -ge $_deadline ] && break
    sleep 2
done
am start -n de.ozerov.fully/.MainActivity 2>/dev/null || true
echo "boot COMPLETADO: $(date)" >> "$LOG"
```

- [ ] **Step 2: Dar permisos de ejecución**

```bash
chmod +x boot/hook.sh
```

- [ ] **Step 3: Eliminar boot/start.sh**

```bash
git rm boot/start.sh
```

- [ ] **Step 4: Verificar**

```bash
ls boot/
# Expected: hook.sh  supervisord.conf.tpl
head -3 boot/hook.sh
# Expected: #!/data/data/com.termux/files/usr/bin/sh
```

- [ ] **Step 5: Commit**

```bash
git add boot/hook.sh
git commit -m "feat: añadir boot/hook.sh v5; eliminar boot/start.sh deprecado"
```

---

### Task 4: install.sh — Reescribir idempotente

**Files:**
- Modify: `install.sh` (reescritura completa)

- [ ] **Step 1: Reescribir install.sh**

```sh
#!/data/data/com.termux/files/usr/bin/sh
# install.sh — setup idempotente de jota-voice en Termux.
# Cada paso comprueba si ya está hecho antes de actuar.
# Ejecutar con: bash ~/jota-voice/install.sh

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

_ok()   { echo "  ✓ $*"; }
_info() { echo "  → $*"; }
_fail() { echo "  ✗ $*" >&2; exit 1; }

echo ""
echo "=== jota-voice install.sh ==="
echo ""

# ── Step 0: Validar configuración ────────────────────────────────────────────
echo "Step 0: Validar configuración"

if [ ! -f "$HOME/jota-env.sh" ]; then
    cp "$REPO_DIR/jota-env.example.sh" "$HOME/jota-env.sh"
    _fail "Creado ~/jota-env.sh desde ejemplo. Rellena DNS_SERVER y vuelve a ejecutar."
fi

if [ ! -f "$REPO_DIR/config.yaml" ]; then
    cp "$REPO_DIR/config.example.yaml" "$REPO_DIR/config.yaml"
    _fail "Creado config.yaml desde ejemplo. Rellena los valores y vuelve a ejecutar."
fi

if grep -q "CAMBIAR" "$HOME/jota-env.sh" 2>/dev/null; then
    _fail "~/jota-env.sh tiene campos 'CAMBIAR' sin rellenar."
fi

if grep -q "CAMBIAR\|YOUR_" "$REPO_DIR/config.yaml" 2>/dev/null; then
    _fail "config.yaml tiene campos sin rellenar."
fi

_ok "Configuración válida"

# ── Step 1: Paquetes Termux ───────────────────────────────────────────────────
echo "Step 1: Paquetes Termux"

_need_pkg() {
    pkg list-installed 2>/dev/null | grep -q "^$1/" && return 1 || return 0
}

PKGS=""
for p in python python-numpy portaudio pulseaudio termux-api git openssh termux-tools ffmpeg; do
    _need_pkg "$p" && PKGS="$PKGS $p"
done

if [ -n "$PKGS" ]; then
    _info "Instalando:$PKGS"
    pkg install -y $PKGS
else
    _ok "Todos los paquetes ya instalados"
fi

# ── Step 2: venv jota-voice ──────────────────────────────────────────────────
echo "Step 2: venv jota-voice"

VENV="$REPO_DIR/.venv"
if [ ! -d "$VENV" ]; then
    _info "Creando venv con --system-site-packages (python-numpy viene de pkg)"
    python -m venv --system-site-packages "$VENV"
fi
_info "Actualizando dependencias pip"
"$VENV/bin/pip" install -q -r "$REPO_DIR/client/requirements.txt" --upgrade
_ok "venv jota-voice listo"

# ── Step 3: venv OWW + modelo ok_nabu ───────────────────────────────────────
echo "Step 3: venv OWW + modelo ok_nabu"

OWW_VENV="$HOME/oww-venv"
OWW_MODEL="$HOME/.local/lib/python3.*/site-packages/wyoming_openwakeword/models/ok_nabu.tflite"

# Comprobar si el modelo existe (glob)
_model_exists() {
    ls $OWW_MODEL 2>/dev/null | head -1 | grep -q "ok_nabu"
}

if [ -d "$OWW_VENV" ] && _model_exists; then
    _ok "OWW venv y modelo ok_nabu ya instalados"
else
    if [ ! -d "$OWW_VENV" ]; then
        _info "Creando oww-venv"
        python -m venv "$OWW_VENV"
    fi
    _info "Instalando wyoming-openwakeword (puede tardar varios minutos en ARM)"
    "$OWW_VENV/bin/pip" install -q wyoming-openwakeword
    _info "Descargando modelo ok_nabu"
    "$OWW_VENV/bin/python3" -c "
from wyoming_openwakeword.download import ensure_model_exists
ensure_model_exists('ok_nabu')
print('Modelo ok_nabu descargado')
"
    _ok "OWW instalado con modelo ok_nabu"
fi

# ── Step 4: supervisord ──────────────────────────────────────────────────────
echo "Step 4: supervisord"

if command -v supervisord >/dev/null 2>&1; then
    _ok "supervisord ya instalado: $(supervisord --version 2>/dev/null | head -1)"
else
    _info "Instalando supervisor via pip"
    pip install -q supervisor
    _ok "supervisord instalado"
fi

# ── Step 5: ~/supervisord.conf ───────────────────────────────────────────────
echo "Step 5: ~/supervisord.conf"

cp "$REPO_DIR/boot/supervisord.conf.tpl" "$HOME/supervisord.conf"
_ok "~/supervisord.conf generado desde boot/supervisord.conf.tpl"

# ── Step 6: Boot hook ────────────────────────────────────────────────────────
echo "Step 6: Boot hook"

BOOT_DIR="$HOME/.termux/boot"
mkdir -p "$BOOT_DIR"
cp "$REPO_DIR/boot/hook.sh" "$BOOT_DIR/jota-voice"
chmod +x "$BOOT_DIR/jota-voice"
_ok "Boot hook instalado en $BOOT_DIR/jota-voice"

# ── Step 7: Smoke test ───────────────────────────────────────────────────────
echo "Step 7: Smoke test"

# Detener supervisord anterior si está corriendo
if [ -S "$HOME/supervisor.sock" ]; then
    _info "Deteniendo supervisord anterior"
    supervisorctl -c "$HOME/supervisord.conf" shutdown 2>/dev/null || true
    sleep 2
fi

_info "Arrancando supervisord"
supervisord -c "$HOME/supervisord.conf"
sleep 10

echo ""
echo "=== Estado de servicios ==="
supervisorctl -c "$HOME/supervisord.conf" status
echo ""

# Verificar que cada servicio está RUNNING o STARTING
_all_ok=true
for svc in oww jota-display jota-voice; do
    status=$(supervisorctl -c "$HOME/supervisord.conf" status "$svc" 2>/dev/null | awk '{print $2}')
    if [ "$status" = "RUNNING" ] || [ "$status" = "STARTING" ]; then
        _ok "$svc: $status"
    else
        echo "  ✗ $svc: $status" >&2
        _all_ok=false
    fi
done

echo ""
if $_all_ok; then
    echo "✓ install.sh completado — todos los servicios arrancados"
else
    echo "⚠ install.sh completado con advertencias — revisar logs con:"
    echo "  tail ~/supervisord.log"
    echo "  tail ~/oww.log"
    echo "  tail ~/jota-voice.log"
fi
```

- [ ] **Step 2: Dar permisos de ejecución**

```bash
chmod +x install.sh
```

- [ ] **Step 3: Verificar sintaxis sh (desde Mac)**

```bash
bash -n install.sh && echo "Sintaxis OK"
```

Expected: `Sintaxis OK`

- [ ] **Step 4: Commit**

```bash
git add install.sh
git commit -m "feat: reescribir install.sh idempotente con validación y supervisord"
```

---

### Task 5: jota-voice CLI (Mac-side, multi-dispositivo)

**Files:**
- Create: `jota-voice` (ejecutable en raíz del repo)

- [ ] **Step 1: Crear jota-voice**

```bash
cat > jota-voice << 'SCRIPT'
#!/usr/bin/env bash
# jota-voice — CLI para gestión del teléfono Android (Termux)
# Uso: jota-voice [device] <comando> [args]

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
DEVICES_DIR="$REPO_DIR/devices"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
_ok()   { printf "${GREEN}✓${NC} %s\n" "$*"; }
_err()  { printf "${RED}✗${NC} %s\n" "$*" >&2; }
_info() { printf "${YELLOW}→${NC} %s\n" "$*"; }

# ── Carga de dispositivos ─────────────────────────────────────────────────────

_list_device_files() {
    ls "$DEVICES_DIR"/*.env 2>/dev/null | grep -v "example.env" || true
}

_load_device() {
    local name="$1"
    local file="$DEVICES_DIR/${name}.env"
    [ -f "$file" ] || { _err "Dispositivo '$name' no encontrado en $DEVICES_DIR/"; exit 1; }
    set -a; . "$file"; set +a
}

_auto_device() {
    local files
    files=$(_list_device_files)
    local count
    count=$(echo "$files" | grep -c "." 2>/dev/null || echo 0)
    [ "$count" -eq 0 ] && { _err "No hay dispositivos en devices/. Crea devices/<nombre>.env"; exit 1; }
    [ "$count" -gt 1 ] && { _err "Hay $count dispositivos — especifica el nombre: jota-voice <device> $*"; exit 1; }
    # Un solo dispositivo → cargarlo
    set -a; . "$files"; set +a
}

# ── Helpers SSH ───────────────────────────────────────────────────────────────

_check_sshpass() {
    command -v sshpass >/dev/null 2>&1 || { _err "sshpass no está instalado. Instalar con: brew install hudochenkov/sshpass/sshpass"; exit 1; }
}

_ssh() {
    sshpass -p "$PHONE_PASS" ssh -o StrictHostKeyChecking=no -p "$PHONE_PORT" "$PHONE_HOST" "$@"
}

_rsync() {
    sshpass -p "$PHONE_PASS" rsync -av \
        -e "ssh -o StrictHostKeyChecking=no -p $PHONE_PORT" \
        --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
        --exclude='config.yaml' --exclude='devices/' \
        "$@"
}

# ── Comandos sin dispositivo ──────────────────────────────────────────────────

cmd_bootstrap() {
    [ -f "$REPO_DIR/bootstrap.sh" ] || { _err "bootstrap.sh no encontrado"; exit 1; }
    bash "$REPO_DIR/bootstrap.sh" "$@"
}

cmd_devices() {
    local files
    files=$(_list_device_files)
    if [ -z "$files" ]; then
        echo "No hay dispositivos configurados en devices/"
        echo "Crea devices/<nombre>.env (ver devices/example.env como plantilla)"
        return
    fi
    echo "Dispositivos configurados:"
    for f in $files; do
        local name
        name=$(basename "$f" .env)
        local host
        host=$(grep "PHONE_HOST" "$f" | cut -d= -f2 | tr -d '"')
        printf "  %-15s %s\n" "$name" "$host"
    done
}

# ── Comandos con dispositivo ──────────────────────────────────────────────────

cmd_setup() {
    _check_sshpass
    _info "[$DEVICE_NAME] Sincronizando repo..."
    _ssh "mkdir -p $PHONE_DIR"
    _rsync "$REPO_DIR/" "$PHONE_HOST:$PHONE_DIR/"
    _info "[$DEVICE_NAME] Ejecutando install.sh..."
    _ssh "bash $PHONE_DIR/install.sh"
}

cmd_status() {
    _check_sshpass
    _info "[$DEVICE_NAME] supervisorctl status"
    _ssh "supervisorctl -c ~/supervisord.conf status" || true
}

cmd_logs() {
    _check_sshpass
    local svc="${1:-jota-voice}"
    local logfile
    case "$svc" in
        jota-voice)   logfile="~/jota-voice.log" ;;
        oww)          logfile="~/oww.log" ;;
        jota-display) logfile="~/jota-display.log" ;;
        supervisord)  logfile="~/supervisord.log" ;;
        boot)         logfile="~/boot.log" ;;
        *) _err "Servicio desconocido: $svc (opciones: jota-voice oww jota-display supervisord boot)"; exit 1 ;;
    esac
    _info "[$DEVICE_NAME] tail -f $logfile"
    _ssh "tail -f $logfile"
}

cmd_restart() {
    _check_sshpass
    local svc="${1:-jota-voice}"
    _info "[$DEVICE_NAME] restart $svc"
    _ssh "supervisorctl -c ~/supervisord.conf restart $svc"
}

cmd_update() {
    _check_sshpass
    _info "[$DEVICE_NAME] Sincronizando código..."
    _ssh "mkdir -p $PHONE_DIR"
    _rsync "$REPO_DIR/" "$PHONE_HOST:$PHONE_DIR/"
    _info "[$DEVICE_NAME] Reiniciando jota-voice..."
    _ssh "supervisorctl -c ~/supervisord.conf restart jota-voice"
    _ok "[$DEVICE_NAME] Actualizado"
}

cmd_adb_port() {
    _check_sshpass
    local port
    port=$(_ssh "cat ~/adb_port.txt 2>/dev/null || echo '(no disponible)'")
    echo "[$DEVICE_NAME] ADB port: $port"
}

# ── --all ─────────────────────────────────────────────────────────────────────

run_all() {
    local subcmd="$1"
    shift
    local files
    files=$(_list_device_files)
    [ -z "$files" ] && { _err "No hay dispositivos configurados"; exit 1; }
    local exit_code=0
    for f in $files; do
        set -a; . "$f"; set +a
        printf "\n${YELLOW}[%s]${NC}\n" "$DEVICE_NAME"
        case "$subcmd" in
            status)  cmd_status  ;;
            logs)    cmd_logs "$@" ;;
            restart) cmd_restart "$@" ;;
            *) _err "--all no soporta el comando '$subcmd'"; exit 1 ;;
        esac || { _err "[$DEVICE_NAME] FALLO"; exit_code=1; }
    done
    exit $exit_code
}

# ── Dispatch ──────────────────────────────────────────────────────────────────

usage() {
    cat << 'EOF'
Uso:
  jota-voice bootstrap             # Setup USB inicial via ADB (una vez)
  jota-voice devices               # Lista dispositivos configurados

  # Un dispositivo (nombre opcional):
  jota-voice setup
  jota-voice status
  jota-voice logs [jota-voice|oww|jota-display|supervisord|boot]
  jota-voice restart [jota-voice|oww|jota-display]
  jota-voice update
  jota-voice adb-port

  # Varios dispositivos (nombre obligatorio):
  jota-voice salon status
  jota-voice cocina logs oww

  # Todos los dispositivos:
  jota-voice --all status
EOF
}

# Sin argumentos → ayuda
[ $# -eq 0 ] && { usage; exit 0; }

# Comandos globales (sin dispositivo)
case "$1" in
    bootstrap) shift; cmd_bootstrap "$@"; exit $? ;;
    devices)   cmd_devices; exit 0 ;;
    --all)
        shift
        [ $# -eq 0 ] && { _err "--all requiere un comando (ej: --all status)"; exit 1; }
        run_all "$@"; exit $?
        ;;
    -h|--help|help) usage; exit 0 ;;
esac

# Determinar si el primer argumento es un nombre de dispositivo o un comando
FIRST="$1"
KNOWN_CMDS="setup status logs restart update adb-port"

_is_command() {
    echo "$KNOWN_CMDS" | tr ' ' '\n' | grep -qx "$1"
}

if _is_command "$FIRST"; then
    # Comando directo → auto-detectar dispositivo
    _auto_device
    CMD="$1"; shift
else
    # Primer arg es nombre de dispositivo
    _load_device "$FIRST"
    shift
    [ $# -eq 0 ] && { _err "Falta el comando para el dispositivo '$FIRST'"; usage; exit 1; }
    CMD="$1"; shift
fi

_check_sshpass
case "$CMD" in
    setup)    cmd_setup ;;
    status)   cmd_status ;;
    logs)     cmd_logs "$@" ;;
    restart)  cmd_restart "$@" ;;
    update)   cmd_update ;;
    adb-port) cmd_adb_port ;;
    *) _err "Comando desconocido: $CMD"; usage; exit 1 ;;
esac
SCRIPT
```

- [ ] **Step 2: Dar permisos de ejecución**

```bash
chmod +x jota-voice
```

- [ ] **Step 3: Verificar sintaxis**

```bash
bash -n jota-voice && echo "Sintaxis OK"
```

Expected: `Sintaxis OK`

- [ ] **Step 4: Test sin dispositivos configurados**

```bash
./jota-voice devices
```

Expected: mensaje indicando que no hay dispositivos (o lista si ya hay alguno).

```bash
./jota-voice status 2>&1 || true
```

Expected (sin devices/*.env): `✗ No hay dispositivos en devices/. Crea devices/<nombre>.env`

- [ ] **Step 5: Commit**

```bash
git add jota-voice
git commit -m "feat: añadir CLI jota-voice multi-dispositivo"
```

---

### Task 6: bootstrap.sh (USB/ADB setup inicial)

**Files:**
- Create: `bootstrap.sh`

- [ ] **Step 1: Crear bootstrap.sh**

```bash
cat > bootstrap.sh << 'SCRIPT'
#!/usr/bin/env bash
# bootstrap.sh — Setup inicial del teléfono Android desde Mac via USB/ADB.
# Precondición: LineageOS instalado, Developer Options activas, USB debugging ON.
# Solo se ejecuta una vez por dispositivo.

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
APK_CACHE="$HOME/.jota-voice/apks"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
_ok()   { printf "${GREEN}✓${NC} %s\n" "$*"; }
_err()  { printf "${RED}✗${NC} %s\n" "$*" >&2; exit 1; }
_info() { printf "${YELLOW}→${NC} %s\n" "$*"; }
_ask()  { printf "${YELLOW}?${NC} %s: " "$*"; }

# URLs de APKs — actualizar si cambian las versiones
TERMUX_URL="https://f-droid.org/repo/com.termux_1000.apk"
TERMUX_BOOT_URL="https://f-droid.org/repo/com.termux.boot_7.apk"
TERMUX_API_URL="https://f-droid.org/repo/com.termux.api_51.apk"
# FullyKiosk: descarga manual — no tiene URL directa pública
FULLYKIOSK_URL="https://www.fully-kiosk.com/downloads/FullyKiosk.apk"

# ── Step 1: Verificar ADB y dispositivo ──────────────────────────────────────
echo ""
echo "=== jota-voice bootstrap.sh ==="
echo ""
_info "Step 1: Verificar ADB y dispositivo conectado"

command -v adb >/dev/null 2>&1 || _err "adb no está instalado. Instalar con: brew install android-platform-tools"

DEVICE_COUNT=$(adb devices | tail -n +2 | grep -c "device$" || true)
[ "$DEVICE_COUNT" -eq 0 ] && _err "No hay dispositivos ADB conectados. Conecta el cable USB y activa USB debugging."
[ "$DEVICE_COUNT" -gt 1 ] && _err "Hay $DEVICE_COUNT dispositivos ADB. Conecta solo uno."
_ok "Dispositivo ADB conectado"

# ── Step 2: Descargar APKs ───────────────────────────────────────────────────
_info "Step 2: Descargar APKs a $APK_CACHE"
mkdir -p "$APK_CACHE"

_download() {
    local name="$1" url="$2"
    local dest="$APK_CACHE/$name"
    if [ -f "$dest" ]; then
        _ok "$name ya descargado"
    else
        _info "Descargando $name..."
        curl -L -o "$dest" "$url" || _err "Error descargando $name de $url"
        _ok "$name descargado"
    fi
}

_download "Termux.apk"      "$TERMUX_URL"
_download "TermuxBoot.apk"  "$TERMUX_BOOT_URL"
_download "TermuxAPI.apk"   "$TERMUX_API_URL"

# FullyKiosk requiere aceptar términos — verificar manualmente si no existe
if [ ! -f "$APK_CACHE/FullyKiosk.apk" ]; then
    echo ""
    echo "  FullyKiosk Browser no está en el cache."
    echo "  Opciones:"
    echo "  A) Descargar ahora (requiere aceptar términos en fullyKiosk.com)"
    _ask "¿Descargar FullyKiosk.apk automáticamente? [s/N]"
    read -r resp
    if [ "$resp" = "s" ] || [ "$resp" = "S" ]; then
        _download "FullyKiosk.apk" "$FULLYKIOSK_URL"
    else
        echo "  Descarga manualmente FullyKiosk Browser APK y colócalo en:"
        echo "  $APK_CACHE/FullyKiosk.apk"
        echo "  Luego vuelve a ejecutar bootstrap.sh"
        exit 1
    fi
fi
_ok "Todos los APKs disponibles"

# ── Step 3: Instalar APKs via ADB ───────────────────────────────────────────
_info "Step 3: Instalar APKs en el teléfono"

_install_apk() {
    local name="$1"
    _info "Instalando $name..."
    adb install -r "$APK_CACHE/$name" && _ok "$name instalado" || _err "Error instalando $name"
}

_install_apk "Termux.apk"
_install_apk "TermuxBoot.apk"
_install_apk "TermuxAPI.apk"
_install_apk "FullyKiosk.apk"

# ── Step 4: Inicializar Termux ──────────────────────────────────────────────
_info "Step 4: Inicializar Termux (primera apertura)"
adb shell am start -n com.termux/.app.TermuxActivity
_info "Esperando inicialización de Termux (15s)..."
sleep 15

# ── Step 5: Setup SSH en Termux ─────────────────────────────────────────────
_info "Step 5: Configurar SSH en Termux"

_ask "Contraseña SSH para el teléfono"
read -rs PHONE_PASS
echo ""
_ask "Confirmar contraseña"
read -rs PHONE_PASS2
echo ""
[ "$PHONE_PASS" != "$PHONE_PASS2" ] && _err "Las contraseñas no coinciden"

# Script de setup mínimo que se ejecutará en Termux
cat > /tmp/termux_bootstrap.sh << EOF
#!/data/data/com.termux/files/usr/bin/sh
echo "=== Termux bootstrap ===" >> ~/bootstrap.log
pkg install -y openssh 2>&1 | tail -3 >> ~/bootstrap.log
echo "$PHONE_PASS" | passwd 2>/dev/null >> ~/bootstrap.log
sshd 2>&1 >> ~/bootstrap.log
echo "SSH READY" >> ~/bootstrap.log
EOF

adb push /tmp/termux_bootstrap.sh /sdcard/termux_bootstrap.sh

# Ejecutar via ADB input (simular teclado en Termux)
adb shell input text "bash /sdcard/termux_bootstrap.sh"
adb shell input keyevent 66  # Enter
_info "Esperando instalación de SSH (30s)..."
sleep 30
_ok "SSH configurado en Termux"

# ── Step 6: Obtener IP del teléfono ─────────────────────────────────────────
_info "Step 6: Detectar IP del teléfono"
PHONE_IP=$(adb shell ip route 2>/dev/null | awk '/wlan0/ && /src/ {for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
[ -z "$PHONE_IP" ] && PHONE_IP=$(adb shell ip addr show wlan0 2>/dev/null | awk '/inet / {split($2,a,"/"); print a[1]}' | head -1)
[ -z "$PHONE_IP" ] && _err "No se pudo detectar IP del teléfono. ¿Está conectado a WiFi?"
_ok "IP detectada: $PHONE_IP"

# ── Step 7: Crear devices/<nombre>.env ──────────────────────────────────────
_info "Step 7: Configurar dispositivo"
_ask "Nombre del dispositivo (ej: salon, cocina)"
read -r DEVICE_NAME
[ -z "$DEVICE_NAME" ] && _err "El nombre no puede estar vacío"

DEVICE_ENV="$REPO_DIR/devices/${DEVICE_NAME}.env"
if [ -f "$DEVICE_ENV" ]; then
    _ask "Ya existe devices/${DEVICE_NAME}.env. ¿Sobreescribir? [s/N]"
    read -r resp
    [ "$resp" != "s" ] && [ "$resp" != "S" ] && { _info "Conservando devices/${DEVICE_NAME}.env existente"; }
fi

cat > "$DEVICE_ENV" << EOF
DEVICE_NAME="${DEVICE_NAME}"
PHONE_HOST="${PHONE_IP}"
PHONE_PORT="8022"
PHONE_PASS="${PHONE_PASS}"
PHONE_DIR="/data/data/com.termux/files/home/jota-voice"
EOF
_ok "Creado devices/${DEVICE_NAME}.env"

# ── Step 8: Enviar jota-env.sh al teléfono ──────────────────────────────────
_info "Step 8: Configurar jota-env.sh en el teléfono"
echo ""
echo "  jota-env.sh configura el entorno del sistema (DNS, etc.)"
_ask "DNS del router local (ej: 192.168.1.1)"
read -r DNS_SERVER
[ -z "$DNS_SERVER" ] && _err "DNS no puede estar vacío"

cat > /tmp/jota-env.sh << EOF
DNS_SERVER="${DNS_SERVER}"
EOF

sshpass -p "$PHONE_PASS" scp -o StrictHostKeyChecking=no -P 8022 \
    /tmp/jota-env.sh "$PHONE_IP:~/jota-env.sh"
_ok "jota-env.sh enviado al teléfono"

# ── Step 9: Enviar config.yaml ───────────────────────────────────────────────
_info "Step 9: Verificar config.yaml"
if [ ! -f "$REPO_DIR/config.yaml" ]; then
    _err "No existe config.yaml. Copia config.example.yaml, rellena los valores y vuelve a ejecutar."
fi
sshpass -p "$PHONE_PASS" scp -o StrictHostKeyChecking=no -P 8022 \
    "$REPO_DIR/config.yaml" "$PHONE_IP:/data/data/com.termux/files/home/jota-voice/config.yaml" 2>/dev/null || true
# (puede fallar si el directorio aún no existe — install.sh lo creará)
_ok "config.yaml listo para deploy"

# ── Step 10: Clonar repo y ejecutar install.sh ──────────────────────────────
_info "Step 10: Clonar repo y ejecutar install.sh"

PHONE_DIR="/data/data/com.termux/files/home/jota-voice"
_ssh_phone() { sshpass -p "$PHONE_PASS" ssh -o StrictHostKeyChecking=no -p 8022 "$PHONE_IP" "$@"; }

# Detectar URL del repo actual
REPO_URL=$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)

if [ -n "$REPO_URL" ]; then
    _info "Clonando desde $REPO_URL"
    _ssh_phone "git clone '$REPO_URL' '$PHONE_DIR' 2>/dev/null || (cd '$PHONE_DIR' && git pull)"
else
    _info "No hay remote — sincronizando via rsync"
    sshpass -p "$PHONE_PASS" rsync -av \
        -e "ssh -o StrictHostKeyChecking=no -p 8022" \
        --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
        --exclude='devices/' \
        "$REPO_DIR/" "$PHONE_IP:$PHONE_DIR/"
fi

# Enviar config.yaml al directorio correcto
sshpass -p "$PHONE_PASS" scp -o StrictHostKeyChecking=no -P 8022 \
    "$REPO_DIR/config.yaml" "$PHONE_IP:$PHONE_DIR/config.yaml"

_info "Ejecutando install.sh en el teléfono (puede tardar varios minutos)"
_ssh_phone "bash '$PHONE_DIR/install.sh'"

# ── Step 11: Resumen ─────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
_ok "Bootstrap completado para '$DEVICE_NAME'"
echo ""
echo "  Dispositivo: $DEVICE_NAME ($PHONE_IP)"
echo "  Config:      devices/${DEVICE_NAME}.env"
echo ""
echo "  Comandos disponibles:"
echo "    ./jota-voice $DEVICE_NAME status"
echo "    ./jota-voice $DEVICE_NAME logs"
echo "    ./jota-voice $DEVICE_NAME update"
echo ""
echo "  Próximo paso: configurar FullyKiosk"
echo "    ver docs/fullyKiosk-setup.md"
echo "═══════════════════════════════════════"
SCRIPT
```

- [ ] **Step 2: Dar permisos y verificar sintaxis**

```bash
chmod +x bootstrap.sh
bash -n bootstrap.sh && echo "Sintaxis OK"
```

Expected: `Sintaxis OK`

- [ ] **Step 3: Commit**

```bash
git add bootstrap.sh
git commit -m "feat: añadir bootstrap.sh para setup USB inicial desde Mac"
```

---

### Task 7: docs/fullyKiosk-setup.md

**Files:**
- Create: `docs/fullyKiosk-setup.md`

- [ ] **Step 1: Crear docs/fullyKiosk-setup.md**

```markdown
# FullyKiosk Browser — Setup Manual

FullyKiosk no puede configurarse por ADB o SSH — requiere interacción directa en el teléfono.
Hacer este setup TRAS ejecutar `bootstrap.sh` o `jota-voice setup`.

## Pasos

1. **Abrir FullyKiosk Browser** en el teléfono (o esperar a que el boot hook lo abra)

2. **Start URL**: `http://localhost:8766`
   - Settings → Web Content → Start URL

3. **Microphone access**: **DISABLED** (CRÍTICO)
   - Settings → Device Management → Microphone Access: Off
   - Si está activado, FullyKiosk bloquea el acceso al micrófono para sles-source y OWW deja de funcionar

4. **Autostart on boot**: **DISABLED**
   - Settings → Device Management → Autostart on Boot: Off
   - El boot hook (`~/.termux/boot/jota-voice`) abre FullyKiosk una vez que jota-display está listo

5. **Kiosk mode**: **ENABLED**
   - Settings → Kiosk Mode → Enable Kiosk Mode: On

6. **Screen timeout**: dejar en valores por defecto
   - El control de pantalla lo gestiona `kiosk_server.py` desde el Mac via ADB

## Verificación

Tras el setup, reiniciar el teléfono. El orden esperado:

1. Boot hook arranca (~30s)
2. PulseAudio + sles-source se cargan
3. supervisord arranca oww, jota-display, jota-voice
4. jota-display está listo en puerto 8766 (~30-60s)
5. Boot hook detecta jota-display → abre FullyKiosk automáticamente
6. FullyKiosk carga `http://localhost:8766` → interfaz kiosk visible

## Diagnóstico

- Boot hook no abre FullyKiosk → revisar `~/boot.log`
- Micrófono no funciona → verificar que Microphone Access está **Off** en FullyKiosk
- Pantalla en negro → comprobar `jota-voice status` y `jota-voice logs jota-display`
```

- [ ] **Step 2: Commit**

```bash
git add docs/fullyKiosk-setup.md
git commit -m "docs: añadir instrucciones manuales de setup de FullyKiosk"
```

---

### Task 8: Eliminar ficheros deprecados

**Files:**
- Delete: `deploy.sh` (reemplazado por `jota-voice update`)

Nota: `boot/start.sh` ya fue eliminado en Task 3. Si existe `jota-watchdog.sh` en el repo, eliminarlo también.

- [ ] **Step 1: Eliminar deploy.sh**

```bash
git rm deploy.sh
```

- [ ] **Step 2: Eliminar jota-watchdog.sh si existe**

```bash
[ -f jota-watchdog.sh ] && git rm jota-watchdog.sh || echo "jota-watchdog.sh no existe en el repo (ok)"
```

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: eliminar deploy.sh y jota-watchdog.sh deprecados"
```

---

### Task 9: Deploy y verificar en el teléfono

Esta tarea requiere el teléfono encendido y SSH accesible.

- [ ] **Step 1: Crear devices/<nombre>.env real**

```bash
# Copiar el ejemplo y rellenar con los valores reales del teléfono
cp devices/example.env devices/salon.env
# Editar: DEVICE_NAME, PHONE_HOST, PHONE_PORT, PHONE_PASS, PHONE_DIR
```

- [ ] **Step 2: Verificar que el CLI detecta el dispositivo**

```bash
./jota-voice devices
```

Expected: lista el dispositivo con su IP.

- [ ] **Step 3: Ejecutar setup (install.sh) en el teléfono**

```bash
./jota-voice setup
```

Expected: output de los 7 pasos de install.sh, terminando con el estado de supervisord.

- [ ] **Step 4: Verificar estado de servicios**

```bash
./jota-voice status
```

Expected:
```
oww                              RUNNING   pid XXXXX, uptime 0:00:XX
jota-display                     RUNNING   pid XXXXX, uptime 0:00:XX
jota-voice                       RUNNING   pid XXXXX, uptime 0:00:XX
```

- [ ] **Step 5: Verificar logs de jota-voice**

```bash
./jota-voice logs
```

Expected: logs del voice_client en tiempo real. Ctrl+C para salir.

- [ ] **Step 6: Reiniciar el teléfono y verificar boot automático**

Apagar y encender el teléfono. Esperar ~2 minutos, luego:

```bash
./jota-voice status
```

Expected: todos los servicios en estado RUNNING.

```bash
./jota-voice logs boot
```

Expected: log del boot hook con líneas como:
```
=== boot <fecha> ===
sshd OK
sles-source OK
supervisord OK
boot COMPLETADO: <fecha>
```

- [ ] **Step 7: Test end-to-end**

Decir "ok, Nabu" al teléfono y verificar:
1. La interfaz kiosk cambia a estado "listening"
2. Aparece la transcripción en la pantalla
3. Se recibe y reproduce la respuesta de voz

- [ ] **Step 8: Commit final**

```bash
git add .
git status  # verificar que devices/*.env NO aparece (está en .gitignore)
git commit -m "feat: bootstrap y gestión robusta del teléfono — implementación completa"
```

---

## Resumen de ficheros

| Fichero | Estado |
|---|---|
| `.gitignore` | Nuevo |
| `devices/example.env` | Nuevo |
| `jota-env.example.sh` | Nuevo |
| `boot/supervisord.conf.tpl` | Nuevo |
| `boot/hook.sh` | Nuevo |
| `boot/start.sh` | Eliminado |
| `install.sh` | Reescrito |
| `jota-voice` | Nuevo (CLI) |
| `bootstrap.sh` | Nuevo |
| `docs/fullyKiosk-setup.md` | Nuevo |
| `deploy.sh` | Eliminado |
