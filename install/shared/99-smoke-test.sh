#!/bin/sh
# 99-smoke-test.sh — Verificación automatizada post-instalación de jota-voice.
#
# Este script automatiza los checks previos al wake-word test físico
# (decir "Ok jota" cerca del Mac). NO sustituye al smoke test manual:
# algunos checks requieren hardware real (micrófono, audio, Dock).
#
# Comprobaciones:
#   1. Servicio launchd cargado (launchctl list com.jota.voice)
#   2. Puerto 10401 (Wyoming OWW Docker) responde
#   3. config.yaml accesible desde la ruta esperada
#   4. Gateway reachable vía HTTP (green-house:8004)
#   5. Últimas líneas del log
#
# Uso:
#   bash install/shared/99-smoke-test.sh
#
# Exit code:
#   0 — todos los checks pasaron
#   1 — al menos un check falló
#
# Notas:
#   - Cross-platform (Mac via launchd, Termux vía fallback básico)
#   - Las constantes (puertos, gateway) se leen del config.yaml si está
#     disponible; si no, se usan los defaults razonables.
#   - Comentarios en español.

set -u  # No usamos -e: queremos reportar TODOS los fallos, no abortar al primero

# ---------- Colores (si es TTY) ----------
if [ -t 1 ]; then
    G="\033[32m"; R="\033[31m"; Y="\033[33m"; C="\033[36m"; N="\033[0m"
else
    G=""; R=""; Y=""; C=""; N=""
fi

_ok()   { printf "  ${G}✓${N} %s\n" "$1"; }
_fail() { printf "  ${R}✗${N} %s\n" "$1" >&2; }
_warn() { printf "  ${Y}!${N} %s\n" "$1"; }
_info() { printf "  ${C}→${N} %s\n" "$1"; }

# ---------- Detección de paths ----------
REPO_DIR="${REPO_DIR:-$HOME/Work/jota-voice}"
DEVICE_ID="${DEVICE_ID:-macbook_sito}"

# En macOS, el config se linka en ~/Library/Application Support/jota-voice/
# En Termux, suele estar en el repo directamente.
if [ -f "$HOME/Library/Application Support/jota-voice/config.yaml" ]; then
    CONFIG="$HOME/Library/Application Support/jota-voice/config.yaml"
elif [ -f "$HOME/.config/jota-voice/config.yaml" ]; then
    CONFIG="$HOME/.config/jota-voice/config.yaml"
elif [ -f "$REPO_DIR/config.yaml" ]; then
    CONFIG="$REPO_DIR/config.yaml"
elif [ -f "$REPO_DIR/devices/${DEVICE_ID}/config.yaml" ]; then
    CONFIG="$REPO_DIR/devices/${DEVICE_ID}/config.yaml"
else
    CONFIG=""
fi

# Defaults — se sobreescriben desde el config si está disponible
OWW_HOST="127.0.0.1"
OWW_PORT=10401
GW_HOST="green-house"
GW_PORT=8004

# Leer config para extraer oww/gateway si está disponible
if [ -n "$CONFIG" ] && [ -f "$CONFIG" ]; then
    # Extracción simple con grep/sed (no requiere yq ni python)
    _cfg_val() {
        # $1 = key_path (ej. "oww.host", "gateway.port")
        local key="$1" val=""
        # Buscamos la clave en formato "clave:" o "  clave:"
        val=$(awk -v k="$key" '
            $0 ~ "^[[:space:]]*"k"[[:space:]]*:" {
                gsub(/^[[:space:]]*/, "", $0)
                sub(k"[[:space:]]*:[[:space:]]*", "", $0)
                # quitar comillas y comentarios inline
                gsub(/^"/, "", $0)
                gsub(/"$/, "", $0)
                gsub(/[[:space:]]*#.*$/, "", $0)
                gsub(/[[:space:]]*$/, "", $0)
                print
                exit
            }
        ' "$CONFIG" 2>/dev/null)
        echo "$val"
    }

    h=$(_cfg_val "oww.host");   [ -n "$h" ] && OWW_HOST="$h"
    p=$(_cfg_val "oww.port");   [ -n "$p" ] && OWW_PORT="$p"
    h=$(_cfg_val "gateway.host"); [ -n "$h" ] && GW_HOST="$h"
    p=$(_cfg_val "gateway.port"); [ -n "$p" ] && GW_PORT="$p"
fi

# Logs en macOS están en ~/Library/Logs/jota-voice/
# En Termux aún no tenemos logging unificado, así que fallback a stdout.log
if [ -f "$HOME/Library/Logs/jota-voice/stdout.log" ]; then
    LOGFILE="$HOME/Library/Logs/jota-voice/stdout.log"
elif [ -f "$HOME/.local/share/jota-voice/stdout.log" ]; then
    LOGFILE="$HOME/.local/share/jota-voice/stdout.log"
else
    LOGFILE=""
fi

# ---------- Banner ----------
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  jota-voice — smoke test automatizado (Task 18)"
echo "════════════════════════════════════════════════════════════"
echo ""
_info "REPO_DIR=$REPO_DIR"
_info "DEVICE_ID=$DEVICE_ID"
[ -n "$CONFIG" ] && _info "config=$CONFIG"
_info "oww=${OWW_HOST}:${OWW_PORT}  gateway=${GW_HOST}:${GW_PORT}"
echo ""

TOTAL=0
PASSED=0

# ---------- Check 1: launchd (macOS) / proceso vivo (otros) ----------
TOTAL=$((TOTAL+1))
_info "Check 1/5 — Servicio launchd cargado"
if command -v launchctl >/dev/null 2>&1; then
    # macOS: launchctl list muestra una línea por servicio cargado
    if launchctl list 2>/dev/null | grep -q "com.jota.voice"; then
        PID=$(launchctl list 2>/dev/null | grep "com.jota.voice" | awk '{print $1}')
        _ok "com.jota.voice cargado (pid=${PID:-N/A})"
        PASSED=$((PASSED+1))
    else
        _fail "com.jota.voice NO está cargado. Ejecuta install/macos/07-launchd.sh"
    fi
else
    # Fallback genérico: buscar el proceso voice_client.py
    if pgrep -f "voice_client.py" >/dev/null 2>&1; then
        _ok "voice_client.py corriendo (pid=$(pgrep -f 'voice_client.py' | head -1))"
        PASSED=$((PASSED+1))
    else
        _fail "voice_client.py no está corriendo. Lánzalo manualmente."
    fi
fi
echo ""

# ---------- Check 2: puerto Wyoming OWW ----------
TOTAL=$((TOTAL+1))
_info "Check 2/5 — Wyoming OWW en ${OWW_HOST}:${OWW_PORT}"
OWW_OK=0
if command -v python3 >/dev/null 2>&1; then
    # Python está disponible — usar socket directo
    python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(3)
r = s.connect_ex(('${OWW_HOST}', ${OWW_PORT}))
s.close()
sys.exit(0 if r == 0 else 1)
" 2>/dev/null && OWW_OK=1
elif command -v nc >/dev/null 2>&1; then
    nc -z -w 3 "${OWW_HOST}" "${OWW_PORT}" 2>/dev/null && OWW_OK=1
elif command -v curl >/dev/null 2>&1; then
    # Wyoming speak esperado: cualquier TCP connect es OK para smoke
    curl -s --connect-timeout 3 "http://${OWW_HOST}:${OWW_PORT}/" >/dev/null 2>&1 && OWW_OK=1
fi

if [ "$OWW_OK" = "1" ]; then
    _ok "OWW escuchando en ${OWW_HOST}:${OWW_PORT}"
    PASSED=$((PASSED+1))
else
    _fail "OWW no responde en ${OWW_HOST}:${OWW_PORT}. ¿Docker Wyoming corriendo?"
fi
echo ""

# ---------- Check 3: config.yaml accesible ----------
TOTAL=$((TOTAL+1))
_info "Check 3/5 — config.yaml accesible"
if [ -n "$CONFIG" ] && [ -f "$CONFIG" ]; then
    SIZE=$(wc -c < "$CONFIG" 2>/dev/null | tr -d ' ')
    _ok "config presente ($SIZE bytes): $CONFIG"
    PASSED=$((PASSED+1))
else
    _fail "config.yaml no encontrado. Ejecuta install/macos/06-configs.sh"
fi
echo ""

# ---------- Check 4: gateway HTTP reachable ----------
TOTAL=$((TOTAL+1))
_info "Check 4/5 — Gateway HTTP ${GW_HOST}:${GW_PORT}"

GW_OK=0
if command -v curl >/dev/null 2>&1; then
    # curl -sI hace HEAD (rápido) con timeout corto
    HTTP_CODE=$(curl -sI --connect-timeout 5 --max-time 8 \
        "http://${GW_HOST}:${GW_PORT}/" 2>/dev/null \
        | head -1 | awk '{print $2}')
    case "$HTTP_CODE" in
        2*|3*|4*|405)
            # 2xx/3xx/4xx/405 todos válidos — al menos el puerto está abierto
            _ok "gateway responde HTTP ${HTTP_CODE:-?}"
            GW_OK=1
            ;;
        *)
            # curl pudo no tener salida (errores de red)
            if [ -z "$HTTP_CODE" ]; then
                GW_OK=0
            else
                _warn "gateway devolvió HTTP ${HTTP_CODE}"
                GW_OK=1
            fi
            ;;
    esac
elif command -v nc >/dev/null 2>&1; then
    nc -z -w 5 "${GW_HOST}" "${GW_PORT}" 2>/dev/null && GW_OK=1
fi

if [ "$GW_OK" = "1" ]; then
    PASSED=$((PASSED+1))
else
    _fail "gateway NO reachable en ${GW_HOST}:${GW_PORT}. Comprueba DNS/red/VPN."
fi
echo ""

# ---------- Check 5: log ----------
TOTAL=$((TOTAL+1))
_info "Check 5/5 — Log reciente"
if [ -n "$LOGFILE" ] && [ -f "$LOGFILE" ]; then
    _ok "log: $LOGFILE"
    echo ""
    echo "    ── últimas 8 líneas ──"
    tail -8 "$LOGFILE" 2>/dev/null | sed 's/^/    /'
    echo "    ───────────────────────"
    PASSED=$((PASSED+1))
elif [ -n "$LOGFILE" ]; then
    _warn "log esperado en $LOGFILE pero no existe aún (servicio arrancando?)"
else
    _warn "no se encontró log (¿servicio aún no ha arrancado?)"
    PASSED=$((PASSED+1))  # No es crítico, no falla el smoke
fi
echo ""

# ---------- Resumen ----------
echo "════════════════════════════════════════════════════════════"
if [ "$PASSED" -eq "$TOTAL" ]; then
    printf "  ${G}OK${N}  — %d/%d checks pasaron\n" "$PASSED" "$TOTAL"
    echo ""
    echo "  Siguiente paso (MANUAL):"
    echo "    Di \"Ok jota\" cerca del Mac y observa el log:"
    echo "      tail -f ~/Library/Logs/jota-voice/stdout.log | grep --line-buffered 'wake\\|RESPONDING\\|transcription'"
    echo ""
    echo "  Si wake-word no dispara, prueba:"
    echo "    launchctl bootout gui/\$(id -u)/com.jota.voice"
    echo "    ~/venvs/jota-voice/bin/python3 $REPO_DIR/client/voice_client.py \"\$CONFIG\""
    echo "════════════════════════════════════════════════════════════"
    exit 0
else
    FAILED=$((TOTAL-PASSED))
    printf "  ${R}FAIL${N} — %d/%d checks pasaron (%d fallaron)\n" "$PASSED" "$TOTAL" "$FAILED"
    echo ""
    echo "  Revisa los ✗ arriba. Comunes:"
    echo "    - Servicio no cargado → bash install/macos/07-launchd.sh"
    echo "    - Wyoming no responde → docker compose -f devices/${DEVICE_ID}/docker-compose.yml up -d"
    echo "    - gateway unreachable → ping ${GW_HOST} o revisa /etc/hosts"
    echo "════════════════════════════════════════════════════════════"
    exit 1
fi
