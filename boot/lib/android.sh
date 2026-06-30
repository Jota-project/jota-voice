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

android_save_adb_port() {
    getprop service.adb.tls.port > "$HOME/adb_port.txt" 2>/dev/null
}

android_open_fullykiosk() {
    am start -n de.ozerov.fully/.MainActivity 2>/dev/null || true
}
