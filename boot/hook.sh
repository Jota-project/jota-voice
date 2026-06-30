#!/data/data/com.termux/files/usr/bin/sh
# jota-voice boot hook v8
# Usa boot/lib/android.sh para helpers

LOG="$HOME/boot.log"
echo "=== boot $(date) ===" >> "$LOG"

. "$HOME/boot/lib/android.sh"

# 1. sshd
android_ensure_sshd

# 2. Guardar puerto ADB
android_save_adb_port

# 3. Lanzar supervisord (gestiona pulseaudio+sles-source, oww, jota-display, jota-voice)
supervisord -c "$HOME/supervisord.conf" \
    && echo "supervisord OK" >> "$LOG" \
    || echo "supervisord ERROR" >> "$LOG"

# 4. Esperar jota-display y abrir FullyKiosk
_deadline=$(( $(date +%s) + 120 ))
while ! (exec 3<>/dev/tcp/127.0.0.1/8766) 2>/dev/null; do
    [ $(date +%s) -ge $_deadline ] && break
    sleep 2
done
android_open_fullykiosk
echo "boot COMPLETADO: $(date)" >> "$LOG"
