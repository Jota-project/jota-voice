#!/bin/sh
# Genera ~/Library/LaunchAgents/com.jota.voice.plist (cliente) y
# com.jota-voice.logrotate.plist (rotación de logs) y los activa.
set -e
. "$(dirname "$0")/00-lib.sh"
. "$(dirname "$0")/launchd-plist.sh"

PYTHON_BIN="$VENV_DIR/bin/python3"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/com.jota.voice.plist"
ROTATE_PLIST="$PLIST_DIR/com.jota-voice.logrotate.plist"
LOG_DIR="$HOME/Library/Logs/jota-voice"
CONFIG="$HOME/Library/Application Support/jota-voice/config.yaml"

if [ ! -x "$PYTHON_BIN" ]; then
    _err "Python no encontrado en $PYTHON_BIN. Ejecuta install/macos/03-venv.sh primero."
    exit 1
fi
if [ ! -f "$CONFIG" ]; then
    _err "config.yaml no encontrado en $CONFIG. Ejecuta install/macos/06-configs.sh primero."
    exit 1
fi

mkdir -p "$PLIST_DIR" "$LOG_DIR"

USER_SHELL_UID="$(id -u)"

# Issue #46: por defecto solo se relanza tras un fallo (SuccessfulExit=false)
# con ThrottleInterval de 30 s, así un error de arranque no entra en un
# crash-loop cada 10 s y "Apagar servicio" (sale con 0) es definitivo hasta
# el próximo inicio de sesión o `launchctl kickstart`. Ver launchd-plist.sh.
#   JOTA_KEEP_ALIVE=on-failure|always|never   (true/false = valores antiguos)
#   JOTA_THROTTLE_INTERVAL=<s>                (defecto 30)
#   JOTA_EXIT_TIMEOUT=<s>                     (defecto 30)
: "${JOTA_KEEP_ALIVE:=on-failure}"

jota_voice_plist "$PYTHON_BIN" "$REPO_DIR" "$CONFIG" "$LOG_DIR" "$JOTA_KEEP_ALIVE" > "$PLIST.tmp" \
    || { rm -f "$PLIST.tmp"; exit 1; }
mv -f "$PLIST.tmp" "$PLIST"
chmod 644 "$PLIST"
_ok "plist generado: $PLIST"
_info "KeepAlive=${JOTA_KEEP_ALIVE}  ThrottleInterval=${JOTA_THROTTLE_INTERVAL:-$JOTA_THROTTLE_INTERVAL_DEFAULT}s  ExitTimeOut=${JOTA_EXIT_TIMEOUT:-$JOTA_EXIT_TIMEOUT_DEFAULT}s"

# Si ya estaba cargado, lo descargamos antes de recargar
launchctl bootout "gui/${USER_SHELL_UID}/com.jota.voice" 2>/dev/null || true
launchctl bootstrap "gui/${USER_SHELL_UID}" "$PLIST"
launchctl enable "gui/${USER_SHELL_UID}/com.jota.voice"
launchctl kickstart -k "gui/${USER_SHELL_UID}/com.jota.voice"
_ok "Servicio com.jota.voice arrancado"

# --- Rotación de logs (issue #46) ---------------------------------------
# Cada hora; rota a partir de JOTA_LOG_MAX_KB (10 MB) y guarda JOTA_LOG_KEEP (3).
jota_logrotate_plist "$REPO_DIR" 3600 "$LOG_DIR" > "$ROTATE_PLIST"
chmod 644 "$ROTATE_PLIST"
launchctl bootout "gui/${USER_SHELL_UID}/com.jota-voice.logrotate" 2>/dev/null || true
if launchctl bootstrap "gui/${USER_SHELL_UID}" "$ROTATE_PLIST"; then
    _ok "Rotación de logs activa (com.jota-voice.logrotate, cada hora, >10 MB, 3 copias)"
else
    _warn "No se pudo cargar com.jota-voice.logrotate; rota a mano con: sh $REPO_DIR/install/macos/rotate-logs.sh"
fi

_info "Logs: tail -f $LOG_DIR/stderr.log   (logging y trazas del cliente van a stderr)"
