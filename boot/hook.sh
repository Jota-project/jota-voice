#!/data/data/com.termux/files/usr/bin/sh
# jota-voice boot hook v6
# Instalado en ~/.termux/boot/jota-voice por install.sh.
# Gestiona las pre-condiciones Android antes de lanzar supervisord.

LOG=$HOME/boot.log
echo "=== boot $(date) ===" >> "$LOG"

# 1. sshd
sshd && echo "sshd OK" >> "$LOG" || echo "sshd ERROR" >> "$LOG"

# 2. DNS — router como primario, 8.8.8.8 como fallback
# Esta configuración viene de install.sh que la toma de config.yaml
{ echo "nameserver 192.168.1.1"; echo "nameserver 8.8.8.8"; } \
    > /data/data/com.termux/files/usr/etc/resolv.conf

# 3. PulseAudio — arrancar y esperar hasta que esté listo (máx 30s)
pulseaudio --start 2>/dev/null || true
for _i in $(seq 1 30); do
    pactl info >/dev/null 2>&1 && break
    sleep 1
done

# 4. Mic warm-up: Android bloquea OpenSL ES hasta que MediaRecorder se usa al menos una vez.
termux-microphone-record -d 1 2>/dev/null || true
sleep 3
termux-microphone-record -q 2>/dev/null || true
sleep 1
pactl load-module module-sles-source 2>/dev/null \
    && echo "sles-source OK" >> "$LOG" \
    || echo "sles-source WARN — supervisord/oww reintentará" >> "$LOG"

# 5. Guardar puerto ADB inalámbrico (cambia en cada reboot)
getprop service.adb.tls.port > "$HOME/adb_port.txt" 2>/dev/null

# 6. Lanzar supervisord (gestiona oww, jota-display, jota-voice)
supervisord -c "$HOME/supervisord.conf" \
    && echo "supervisord OK" >> "$LOG" \
    || echo "supervisord ERROR" >> "$LOG"

# 7. Esperar jota-display (puerto 8766) → abrir FullyKiosk
_deadline=$(( $(date +%s) + 120 ))
while ! (exec 3<>/dev/tcp/127.0.0.1/8766) 2>/dev/null; do
    [ $(date +%s) -ge $_deadline ] && break
    sleep 2
done
am start -n de.ozerov.fully/.MainActivity 2>/dev/null || true
echo "boot COMPLETADO: $(date)" >> "$LOG"