#!/bin/sh
# rotate-logs.sh — Rotación de logs de los LaunchAgents de jota-voice (issue #46).
#
# launchd no rota StandardOutPath/StandardErrorPath y newsyslog.d exige root,
# así que lo hace este script, lanzado cada hora por el agente
# com.jota-voice.logrotate (07-launchd.sh).
#
# Estrategia copy-truncate: launchd abre los logs en modo append, así que
# truncar el original es seguro con el proceso vivo (sigue escribiendo al
# final, ahora en el byte 0). Se pierden como mucho las líneas escritas
# entre la copia y el truncado.
#
# Uso: rotate-logs.sh [fichero...]   (sin argumentos: los logs por defecto)
# Variables: JOTA_LOG_MAX_KB (defecto 10240 = 10 MB), JOTA_LOG_KEEP (defecto 3).
set -u

MAX_KB="${JOTA_LOG_MAX_KB:-10240}"
KEEP="${JOTA_LOG_KEEP:-3}"

if [ "$#" -eq 0 ]; then
    set -- "$HOME/Library/Logs/jota-voice/stdout.log" \
           "$HOME/Library/Logs/jota-voice/stderr.log" \
           "$HOME/Library/Logs/jota-voice/logrotate.log" \
           "$HOME/Library/Logs/com.jota-voice.wyoming-oww.log"
fi

for log in "$@"; do
    [ -f "$log" ] || continue
    bytes=$(wc -c < "$log" 2>/dev/null | tr -d ' ')
    [ -n "$bytes" ] || continue
    size_kb=$((bytes / 1024))
    [ "$size_kb" -gt "$MAX_KB" ] || continue

    i="$KEEP"
    rm -f "$log.$i"
    while [ "$i" -gt 1 ]; do
        prev=$((i - 1))
        [ -f "$log.$prev" ] && mv -f "$log.$prev" "$log.$i"
        i="$prev"
    done
    cp -p "$log" "$log.1" && : > "$log"
    echo "$(date '+%Y-%m-%d %H:%M:%S') rotado $log (${size_kb} KB)"
done
exit 0
