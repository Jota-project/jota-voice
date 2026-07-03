#!/bin/sh
# Genera ~/Library/LaunchAgents/com.jota.voice.plist y lo activa.
set -e
. "$(dirname "$0")/00-lib.sh"

PYTHON_BIN="$VENV_DIR/bin/python3"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/com.jota.voice.plist"
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

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.jota.voice</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${REPO_DIR}/client/voice_client.py</string>
        <string>${CONFIG}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ProcessType</key><string>Interactive</string>
    <key>StandardOutPath</key><string>${LOG_DIR}/stdout.log</string>
    <key>StandardErrorPath</key><string>${LOG_DIR}/stderr.log</string>
    <key>WorkingDirectory</key><string>${REPO_DIR}</string>
</dict></plist>
EOF

chmod 644 "$PLIST"
_ok "plist generado: $PLIST"

# Si ya estaba cargado, lo descargamos antes de recargar
launchctl bootout "gui/${USER_SHELL_UID}/com.jota.voice" 2>/dev/null || true
launchctl bootstrap "gui/${USER_SHELL_UID}" "$PLIST"
launchctl enable "gui/${USER_SHELL_UID}/com.jota.voice"
launchctl kickstart -k "gui/${USER_SHELL_UID}/com.jota.voice"
_ok "Servicio com.jota.voice arrancado"
_info "Logs: tail -f $LOG_DIR/stdout.log"