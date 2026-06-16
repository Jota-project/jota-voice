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