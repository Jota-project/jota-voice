#!/data/data/com.termux/files/usr/bin/sh
# jota-voice boot hook v9
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

# 4. Esperar jota-display (máx 30s) y abrir FullyKiosk.
# Independientemente de si jota-display arrancó, FK debe abrirse — la UI
# puede mostrar su propio boot screen mientras supervisord lo arranca.
_deadline=$(( $(date +%s) + 30 ))
while ! (exec 3<>/dev/tcp/127.0.0.1/8766) 2>/dev/null; do
    [ $(date +%s) -ge $_deadline ] && break
    sleep 2
done
android_open_fullykiosk

# 5. Esperar a jota-display real (puerto respondiendo) hasta 2 min, en background.
# Si arranque, log. Si no, supervisord lo está reintentando solo.
(
    _deadline2=$(( $(date +%s) + 120 ))
    while ! (exec 3<>/dev/tcp/127.0.0.1/8766) 2>/dev/null; do
        [ $(date +%s) -ge $_deadline2 ] && {
            echo "jota-display: timeout 120s, supervisord reintenta" >> "$LOG"
            break
        }
        sleep 5
    done
    (exec 3<>/dev/tcp/127.0.0.1/8766) 2>/dev/null && \
        echo "jota-display: UP (puerto 8766)" >> "$LOG"
) &

echo "boot COMPLETADO: $(date)" >> "$LOG"
