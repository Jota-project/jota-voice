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
