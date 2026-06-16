# Script Modularization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modularizar los scripts en unidades con responsabilidad única, eliminando valores hardcoded.

**Architecture:** Scripts reorganizados en `lib/` (helpers reutilizables), `install/` (pasos de instalación), y `boot/lib/` (helpers Android). Deploy y hooks del kiosk actualizados para usar config.

**Tech Stack:** Bash/sh, SSH, rsync

---

## Task 1: Crear lib/output.sh

**Files:**
- Create: `lib/output.sh`

- [ ] **Step 1: Crear lib/output.sh**

```sh
#!/bin/sh
# output.sh — Helpers de output reutilizables

_ok()   { echo "  ✓ $*"; }
_info() { echo "  → $*"; }
_err()  { echo "  ✗ $*" >&2; }
_fail() { echo "  ✗ $*" >&2; exit 1; }
```

- [ ] **Step 2: Commit**

```bash
mkdir -p lib
git add lib/output.sh
git commit -m "feat(lib): añadir output.sh con helpers _ok, _info, _err"
```

---

## Task 2: Crear lib/yaml.sh

**Files:**
- Create: `lib/yaml.sh`

- [ ] **Step 1: Crear lib/yaml.sh**

```sh
#!/bin/sh
# yaml.sh — Parseo de config.yaml
# Uso: source lib/yaml.sh

YAML_FILE="${REPO_DIR}/config.yaml"

yaml_get() {
    local key="$1"
    grep "^${key}:" "$YAML_FILE" 2>/dev/null | sed 's/^[^:]*: *//' | tr -d '"'
}

yaml_get_hosts() {
    local in_hosts=false
    local ip="" name=""
    grep -A 30 "^hosts:" "$YAML_FILE" 2>/dev/null | while read -r line; do
        case "$line" in
            "hosts:")
                in_hosts=true
                ;;
            "")
                in_hosts=false
                ;;
            *ip:*)
                ip=$(echo "$line" | sed 's/.*ip: *"\([^"]*\)".*/\1/')
                ;;
            *name:*)
                name=$(echo "$line" | sed 's/.*name: *"\([^"]*\)".*/\1/')
                if [ -n "$ip" ] && [ -n "$name" ]; then
                    echo "${ip} ${name}"
                    ip=""; name=""
                fi
                ;;
        esac
    done
}
```

- [ ] **Step 2: Commit**

```bash
git add lib/yaml.sh
git commit -m "feat(lib): añadir yaml.sh con parseo de config.yaml"
```

---

## Task 3: Crear boot/lib/android.sh

**Files:**
- Create: `boot/lib/android.sh`

- [ ] **Step 1: Crear boot/lib/android.sh**

```sh
#!/data/data/com.termux/files/usr/bin/sh
# android.sh — Helpers Android/Termux para boot hook

LOG="$HOME/boot.log"

android_ensure_sshd() {
    if ! pgrep -x sshd >/dev/null 2>&1; then
        sshd && echo "sshd OK" >> "$LOG" || echo "sshd ERROR" >> "$LOG"
    else
        echo "sshd already running" >> "$LOG"
    fi
}

android_set_dns() {
    local router="${1:-192.168.1.1}"
    {
        echo "nameserver $router"
        echo "nameserver 8.8.8.8"
    } > /data/data/com.termux/files/usr/etc/resolv.conf
}

android_wait_pulseaudio() {
    pulseaudio --start 2>/dev/null || true
    local i
    for i in $(seq 1 30); do
        pactl info >/dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}

android_warmup_mic() {
    termux-microphone-record -d 1 2>/dev/null || true
    sleep 3
    termux-microphone-record -q 2>/dev/null || true
    sleep 1
}

android_load_sles_source() {
    pactl load-module module-sles-source 2>/dev/null \
        && echo "sles-source OK" >> "$LOG" \
        || echo "sles-source WARN" >> "$LOG"
}

android_save_adb_port() {
    getprop service.adb.tls.port > "$HOME/adb_port.txt" 2>/dev/null
}

android_open_fullykiosk() {
    am start -n de.ozerov.fully/.MainActivity 2>/dev/null || true
}
```

- [ ] **Step 2: Commit**

```bash
mkdir -p boot/lib
git add boot/lib/android.sh
git commit -m "feat(boot/lib): añadir android.sh con helpers para boot hook"
```

---

## Task 4: Refactorizar boot/hook.sh para usar boot/lib/android.sh

**Files:**
- Modify: `boot/hook.sh` (reescritura completa)

- [ ] **Step 1: Reescribir boot/hook.sh**

```sh
#!/data/data/com.termux/files/usr/bin/sh
# jota-voice boot hook v7
# Usa boot/lib/android.sh para helpers

LOG="$HOME/boot.log"
echo "=== boot $(date) ===" >> "$LOG"

. "$HOME/boot/lib/android.sh"

# 1. sshd
android_ensure_sshd

# 2. DNS (opcional, solo si hay config)
if [ -f "$HOME/.jota-dns" ]; then
    android_set_dns "$(cat "$HOME/.jota-dns")"
fi

# 3. PulseAudio + mic warm-up
android_wait_pulseaudio
android_warmup_mic
android_load_sles_source

# 4. Guardar puerto ADB
android_save_adb_port

# 5. Lanzar supervisord
supervisord -c "$HOME/supervisord.conf" \
    && echo "supervisord OK" >> "$LOG" \
    || echo "supervisord ERROR" >> "$LOG"

# 6. Esperar jota-display y abrir FullyKiosk
_deadline=$(( $(date +%s) + 120 ))
while ! (exec 3<>/dev/tcp/127.0.0.1/8766) 2>/dev/null; do
    [ $(date +%s) -ge $_deadline ] && break
    sleep 2
done
android_open_fullykiosk
echo "boot COMPLETADO: $(date)" >> "$LOG"
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x boot/hook.sh
```

- [ ] **Step 3: Commit**

```bash
git add boot/hook.sh
git commit -m "refactor(boot): hook.sh v7 usa boot/lib/android.sh"
```

---

## Task 5: Crear install/01-packages.sh

**Files:**
- Create: `install/01-packages.sh`

- [ ] **Step 1: Crear install/01-packages.sh**

```sh
#!/bin/sh
set -e
source ../lib/output.sh

PKGS="python python-numpy portaudio pulseaudio termux-api git openssh termux-tools ffmpeg"

_need_pkg() {
    pkg list-installed 2>/dev/null | grep -q "^$1/" && return 1 || return 0
}

_check() {
    return 0  # Siempre ejecutar, pkg es idempotente
}

_apply() {
    local to_install=""
    for p in $PKGS; do
        _need_pkg "$p" && to_install="$to_install $p"
    done
    if [ -n "$to_install" ]; then
        _info "Instalando:$to_install"
        pkg install -y $to_install
    fi
    _ok "Paquetes actualizados"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check
else
    _apply
fi
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x install/01-packages.sh
```

- [ ] **Step 3: Commit**

```bash
git add install/01-packages.sh
git commit -m "feat(install): separar 01-packages.sh"
```

---

## Task 6: Crear install/02-hosts.sh

**Files:**
- Create: `install/02-hosts.sh`

- [ ] **Step 1: Crear install/02-hosts.sh**

```sh
#!/bin/sh
set -e
source ../lib/output.sh
source ../lib/yaml.sh

HOSTS_FILE="/data/data/com.termux/files/usr/etc/hosts"

_check() {
    # Verificar si los hosts de config ya están en /etc/hosts
    local missing=false
    while read -r ip name; do
        grep -q "${ip} ${name}" "$HOSTS_FILE" 2>/dev/null || missing=true
    done << EOF
$(yaml_get_hosts)
EOF
    [ "$missing" = "false" ]
}

_apply() {
    _info "Configurando /etc/hosts..."
    {
        echo "127.0.0.1 localhost"
        echo "::1 ip6-localhost"
        yaml_get_hosts
    } > "$HOSTS_FILE"
    _ok "/etc/hosts configurado"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "Hosts ya configurados" || exit 1
else
    _apply
fi
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x install/02-hosts.sh
```

- [ ] **Step 3: Commit**

```bash
git add install/02-hosts.sh
git commit -m "feat(install): separar 02-hosts.sh"
```

---

## Task 7: Crear install/03-venv.sh

**Files:**
- Create: `install/03-venv.sh`

- [ ] **Step 1: Crear install/03-venv.sh**

```sh
#!/bin/sh
set -e
source ../lib/output.sh

VENV="${REPO_DIR}/.venv"

_check() {
    [ -d "$VENV" ] && [ -f "$VENV/bin/pip" ]
}

_apply() {
    if [ ! -d "$VENV" ]; then
        _info "Creando venv con --system-site-packages"
        python -m venv --system-site-packages "$VENV"
    fi
    _info "Actualizando dependencias pip"
    "$VENV/bin/pip" install -q -r "$REPO_DIR/client/requirements.txt" --upgrade
    _ok "venv listo"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "venv ya existe" || exit 1
else
    _apply
fi
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x install/03-venv.sh
```

- [ ] **Step 3: Commit**

```bash
git add install/03-venv.sh
git commit -m "feat(install): separar 03-venv.sh"
```

---

## Task 8: Crear install/04-oww.sh

**Files:**
- Create: `install/04-oww.sh`

- [ ] **Step 1: Crear install/04-oww.sh**

```sh
#!/bin/sh
set -e
source ../lib/output.sh

OWW_VENV="$HOME/oww-venv"
OWW_MODEL="$HOME/.local/lib/python3.*/site-packages/wyoming_openwakeword/models/ok_nabu.tflite"

_model_exists() {
    ls $OWW_MODEL 2>/dev/null | head -1 | grep -q "ok_nabu"
}

_check() {
    [ -d "$OWW_VENV" ] && _model_exists
}

_apply() {
    if [ ! -d "$OWW_VENV" ]; then
        _info "Creando oww-venv"
        python -m venv "$OWW_VENV"
    fi
    _info "Instalando wyoming-openwakeword"
    "$OWW_VENV/bin/pip" install -q wyoming-openwakeword
    _info "Descargando modelo ok_nabu"
    "$OWW_VENV/bin/python3" -c "
from wyoming_openwakeword.download import ensure_model_exists
ensure_model_exists('ok_nabu')
print('Modelo ok_nabu descargado')
"
    _ok "OWW instalado con modelo ok_nabu"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "OWW ya instalado" || exit 1
else
    _apply
fi
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x install/04-oww.sh
```

- [ ] **Step 3: Commit**

```bash
git add install/04-oww.sh
git commit -m "feat(install): separar 04-oww.sh"
```

---

## Task 9: Crear install/05-supervisord.sh

**Files:**
- Create: `install/05-supervisord.sh`

- [ ] **Step 1: Crear install/05-supervisord.sh**

```sh
#!/bin/sh
set -e
source ../lib/output.sh

_check() {
    command -v supervisord >/dev/null 2>&1
}

_apply() {
    _info "Instalando supervisor via pip"
    pip install -q supervisor
    _ok "supervisord instalado"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "supervisord ya instalado" || exit 1
else
    _apply
fi
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x install/05-supervisord.sh
```

- [ ] **Step 3: Commit**

```bash
git add install/05-supervisord.sh
git commit -m "feat(install): separar 05-supervisord.sh"
```

---

## Task 10: Crear install/06-configs.sh

**Files:**
- Create: `install/06-configs.sh`

- [ ] **Step 1: Crear install/06-configs.sh**

```sh
#!/bin/sh
set -e
source ../lib/output.sh

_check() {
    [ -f "$HOME/supervisord.conf" ]
}

_apply() {
    cp "$REPO_DIR/boot/supervisord.conf.tpl" "$HOME/supervisord.conf"
    _ok "supervisord.conf generado"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "Configs ya copiadas" || exit 1
else
    _apply
fi
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x install/06-configs.sh
```

- [ ] **Step 3: Commit**

```bash
git add install/06-configs.sh
git commit -m "feat(install): separar 06-configs.sh"
```

---

## Task 11: Crear install/07-boot.sh

**Files:**
- Create: `install/07-boot.sh`

- [ ] **Step 1: Crear install/07-boot.sh**

```sh
#!/bin/sh
set -e
source ../lib/output.sh

_check() {
    [ -x "$HOME/.termux/boot/jota-voice" ]
}

_apply() {
    mkdir -p "$HOME/.termux/boot"
    cp "$REPO_DIR/boot/hook.sh" "$HOME/.termux/boot/jota-voice"
    chmod +x "$HOME/.termux/boot/jota-voice"

    mkdir -p "$HOME/boot/lib"
    cp "$REPO_DIR/boot/lib/android.sh" "$HOME/boot/lib/android.sh"
    chmod +x "$HOME/boot/lib/android.sh"

    _ok "Boot hook instalado"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "Boot hook ya instalado" || exit 1
else
    _apply
fi
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x install/07-boot.sh
```

- [ ] **Step 3: Commit**

```bash
git add install/07-boot.sh
git commit -m "feat(install): separar 07-boot.sh"
```

---

## Task 12: Crear install/99-smoke-test.sh

**Files:**
- Create: `install/99-smoke-test.sh`

- [ ] **Step 1: Crear install/99-smoke-test.sh**

```sh
#!/bin/sh
set -e
source ../lib/output.sh

_apply() {
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
        echo "⚠ install.sh completado con advertencias"
        exit 1
    fi
}

_apply
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x install/99-smoke-test.sh
```

- [ ] **Step 3: Commit**

```bash
git add install/99-smoke-test.sh
git commit -m "feat(install): separar 99-smoke-test.sh"
```

---

## Task 13: Reescribir install.sh como runner

**Files:**
- Modify: `install.sh` (reescritura completa)

- [ ] **Step 1: Reescribir install.sh como runner**

```sh
#!/data/data/com.termux/files/usr/bin/sh
# install.sh — Runner idempotente para setup de jota-voice en Termux
# Ejecuta cada paso en install/ si no está ya hecho

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

source "$REPO_DIR/lib/output.sh"

# ── Validación ────────────────────────────────────────────────
if [ ! -f "$REPO_DIR/config.yaml" ]; then
    _fail "No existe config.yaml. Ejecuta 'jota-voice init' en el Mac."
fi

if grep -q "RELLENAR\|YOUR_" "$REPO_DIR/config.yaml" 2>/dev/null; then
    _fail "config.yaml tiene campos sin rellenar."
fi

_ok "Configuración válida"

# ── Ejecutar pasos ────────────────────────────────────────────
for step in install/*.sh; do
    name=$(basename "$step")
    echo ""
    echo "=== $name ==="
    . "$step"
done
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x install.sh
```

- [ ] **Step 3: Commit**

```bash
git add install.sh
git commit -m "refactor(install): install.sh es ahora runner idempotente"
```

---

## Task 14: Actualizar kiosk/deploy.sh para usar devices/*.env

**Files:**
- Modify: `kiosk/deploy.sh`

- [ ] **Step 1: Actualizar kiosk/deploy.sh**

```sh
#!/usr/bin/env bash
# deploy.sh — Despliega el kiosk al teléfono vía SSH
# Uso: ./deploy.sh [device]
set -e

REPO_DIR="$(cd "$(dirname "$0")"/.. && pwd)"
DEVICE="${1:-}"

if [ -z "$DEVICE" ]; then
    # Auto-detectar un solo dispositivo
    files=$(find "$REPO_DIR/devices" -maxdepth 1 -name "*.env" -not -name "example.env" 2>/dev/null)
    count=$(echo "$files" | grep -c "^" || true)
    if [ "$count" -eq 0 ]; then
        echo "No hay dispositivos. Crea devices/<nombre>.env"
        exit 1
    fi
    DEVICE=$(basename "$files" .env)
fi

DEVICE_FILE="$REPO_DIR/devices/${DEVICE}.env"
if [ ! -f "$DEVICE_FILE" ]; then
    echo "Dispositivo '$DEVICE' no encontrado"
    exit 1
fi

source "$DEVICE_FILE"

SSH="sshpass -p $PHONE_PASS ssh -o StrictHostKeyChecking=no -p $PHONE_PORT $PHONE_HOST"
SCP="sshpass -p $PHONE_PASS scp -o StrictHostKeyChecking=no -P $PHONE_PORT"

echo "→ Desplegando kiosk a $DEVICE ($PHONE_HOST)..."
$SSH "mkdir -p $PHONE_DIR/kiosk/hooks"

echo "→ Copiando server.py, index.html, manifest.json..."
$SCP "$REPO_DIR/kiosk/server.py" "$PHONE_HOST:$PHONE_DIR/kiosk/server.py"
$SCP "$REPO_DIR/kiosk/index.html" "$PHONE_HOST:$PHONE_DIR/kiosk/index.html"
$SCP "$REPO_DIR/kiosk/manifest.json" "$PHONE_HOST:$PHONE_DIR/kiosk/manifest.json"

echo "→ Copiando hooks..."
for hook in "$REPO_DIR/kiosk/hooks"/*.sh; do
    [ -f "$hook" ] || continue
    $SCP "$hook" "$PHONE_HOST:$PHONE_DIR/kiosk/hooks/"
done

echo "→ Haciendo ejecutables..."
$SSH "chmod +x $PHONE_DIR/kiosk/hooks/*.sh"

echo "✓ Deploy completo"
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x kiosk/deploy.sh
```

- [ ] **Step 3: Commit**

```bash
git add kiosk/deploy.sh
git commit -m "refactor(kiosk): deploy.sh usa devices/*.env"
```

---

## Task 15: Actualizar kiosk/hooks para usar display URL de archivo

**Files:**
- Modify: `kiosk/hooks/on_detection.sh`
- Modify: `kiosk/hooks/on_transcript.sh`
- Modify: `kiosk/hooks/on_synthesize.sh`
- Modify: `kiosk/hooks/on_stt_start.sh`

- [ ] **Step 1: Actualizar on_detection.sh**

```sh
#!/data/data/com.termux/files/usr/bin/sh
DISPLAY_URL="${1:-$(cat ~/.jota-display-url 2>/dev/null || echo 'http://127.0.0.1:8766')}"
curl -s -X POST "$DISPLAY_URL/state" \
  -H 'Content-Type: application/json' \
  -d '{"state":"listening","text":""}' &
```

- [ ] **Step 2: Actualizar on_transcript.sh**

```sh
#!/data/data/com.termux/files/usr/bin/sh
TEXT="$1"
DISPLAY_URL="${2:-$(cat ~/.jota-display-url 2>/dev/null || echo 'http://127.0.0.1:8766')}"
PAYLOAD=$(printf '{"state":"thinking","text":"%s"}' "$(echo "$TEXT" | sed 's/"/\\"/g')")
curl -s -X POST "$DISPLAY_URL/state" \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD" &
```

- [ ] **Step 3: Actualizar on_synthesize.sh**

```sh
#!/data/data/com.termux/files/usr/bin/sh
TEXT="$1"
DISPLAY_URL="${2:-$(cat ~/.jota-display-url 2>/dev/null || echo 'http://127.0.0.1:8766')}"
PAYLOAD=$(printf '{"state":"response","text":"%s"}' "$(echo "$TEXT" | sed 's/"/\\"/g')")
curl -s -X POST "$DISPLAY_URL/state" \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD" &
```

- [ ] **Step 4: Actualizar on_stt_start.sh**

```sh
#!/data/data/com.termux/files/usr/bin/sh
DISPLAY_URL="${1:-$(cat ~/.jota-display-url 2>/dev/null || echo 'http://127.0.0.1:8766')}"
curl -s -X POST "$DISPLAY_URL/state" \
  -H 'Content-Type: application/json' \
  -d '{"state":"listening","text":""}' &
```

- [ ] **Step 5: chmod +x**

```bash
chmod +x kiosk/hooks/*.sh
```

- [ ] **Step 6: Commit**

```bash
git add kiosk/hooks/*.sh
git commit -m "refactor(kiosk): hooks leen display URL de ~/.jota-display-url"
```

---

## Task 16: Actualizar install/06-configs.sh para crear ~/.jota-display-url

**Files:**
- Modify: `install/06-configs.sh`

- [ ] **Step 1: Actualizar para escribir ~/.jota-display-url**

```sh
#!/bin/sh
set -e
source ../lib/output.sh
source ../lib/yaml.sh

_check() {
    [ -f "$HOME/supervisord.conf" ] && [ -f "$HOME/.jota-display-url" ]
}

_apply() {
    cp "$REPO_DIR/boot/supervisord.conf.tpl" "$HOME/supervisord.conf"
    _ok "supervisord.conf generado"

    # Guardar display URL para hooks
    local display_url
    display_url=$(yaml_get display.url || echo 'http://127.0.0.1:8766')
    echo "$display_url" > "$HOME/.jota-display-url"
    _ok "display URL guardada: $display_url"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "Configs ya copiadas" || exit 1
else
    _apply
fi
```

- [ ] **Step 2: Commit**

```bash
git add install/06-configs.sh
git commit -m "feat(install): 06-configs.sh crea ~/.jota-display-url"
```

---

## Task 17: Actualizar jota-voice init para preguntar display.url

**Files:**
- Modify: `jota-voice` (cmd_init function)

- [ ] **Step 1: Añadir pregunta por display.url en init**

Buscar en cmd_init la sección de hosts y añadir después:

```bash
    # 2b. Display URL
    echo ""
    _info "¿URL del display? (default: http://127.0.0.1:8766)"
    printf "  Display URL [http://127.0.0.1:8766]: "
    read -r DISPLAY_URL
    DISPLAY_URL="${DISPLAY_URL:-http://127.0.0.1:8766}"
    if [ "$DISPLAY_URL" != "http://127.0.0.1:8766" ]; then
        sed -i '' "s|url: \"http://127.0.0.1:8766\"|url: \"$DISPLAY_URL\"|" "$REPO_DIR/config.yaml"
        _ok "Display URL actualizada"
    fi
```

- [ ] **Step 2: Commit**

```bash
git add jota-voice
git commit -m "feat(init): preguntar display.url y guardarlo en config.yaml"
```

---

## Task 18: Commit final

- [ ] **Step 1: Verificar que todo compila**

```bash
bash -n install.sh && echo "install.sh OK"
bash -n lib/*.sh && echo "lib/*.sh OK"
bash -n install/*.sh && echo "install/*.sh OK"
bash -n boot/lib/android.sh && echo "boot/lib/android.sh OK"
bash -n kiosk/deploy.sh && echo "kiosk/deploy.sh OK"
```

- [ ] **Step 2: Commit final**

```bash
git add -A
git status
git commit -m "feat: scripts modularizados con lib/, install/, boot/lib/"
```
