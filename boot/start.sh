#!/data/data/com.termux/files/usr/bin/sh
# Arranca jota-voice-client.
# Llamado desde ~/.termux/boot/ al arrancar Android.
#
# Dependencias externas que deben estar corriendo:
#   - wyoming-openwakeword (en worker-01, puerto 10401) — no se gestiona aquí
#   - jota-gateway (en green-house, puerto 8004)
#   - jota-display server (puerto 8766) — arranca si no está

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$HOME/jota-voice.log"

echo "=== jota-voice boot $(date) ===" >> "$LOG"

# ── 1. Cargar entorno ────────────────────────────────────────────────────────
if [ -f "$HOME/jota-env.sh" ]; then
    . "$HOME/jota-env.sh"
fi

# ── 2. Esperar PulseAudio + sles-source ──────────────────────────────────────
_deadline=$(( $(date +%s) + 120 ))
until pactl list short modules 2>/dev/null | grep -q "module-sles-source"; do
    if [ $(date +%s) -ge $_deadline ]; then
        echo "[boot] sles-source timeout — arrancando igualmente" >> "$LOG"
        break
    fi
    sleep 5
done
echo "[boot] PulseAudio OK" >> "$LOG"

# ── 3. Activar venv ───────────────────────────────────────────────────────────
VENV="$REPO_DIR/.venv"
if [ ! -d "$VENV" ]; then
    echo "[boot] ERROR: venv no existe — ejecutar install.sh primero" >> "$LOG"
    exit 1
fi
. "$VENV/bin/activate"

# ── 4. Arrancar jota-voice-client ────────────────────────────────────────────
echo "[boot] Arrancando jota-voice-client" >> "$LOG"
cd "$REPO_DIR"
exec python client/voice_client.py config.yaml >> "$LOG" 2>&1
