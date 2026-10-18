#!/bin/sh
# launchd-plist.sh — Generadores de los plist de los LaunchAgents (issue #46).
# Funciones puras (escriben el plist en stdout) para poder validarlas con
# plutil en los tests sin tocar launchd.

# Política de reinicio del cliente, JOTA_KEEP_ALIVE:
#   on-failure (defecto) — solo si sale con error o muere por señal
#                          (KeepAlive.SuccessfulExit=false). "Apagar servicio"
#                          del menú sale con 0 y ya no se relanza.
#   always               — siempre (comportamiento anterior a #46).
#   never                — nunca.
#   true / false         — alias de on-failure / never (valores antiguos).
jota_keepalive_xml() {
    case "${1:-on-failure}" in
        on-failure|true) echo '<dict><key>SuccessfulExit</key><false/></dict>' ;;
        always)          echo '<true/>' ;;
        never|false)     echo '<false/>' ;;
        *) echo "JOTA_KEEP_ALIVE inválido: '$1' (on-failure|always|never)" >&2; return 1 ;;
    esac
}

# Segundos mínimos entre relanzamientos (launchd usa 10 si no se declara).
JOTA_THROTTLE_INTERVAL_DEFAULT=30
# Margen de apagado ordenado tras SIGTERM antes del SIGKILL (launchd: 20 s).
# El cliente puede tardar hasta ~10 s en el join del hilo asyncio con el
# menubar Cocoa, más ~2 s del stop del display; 30 s deja margen.
JOTA_EXIT_TIMEOUT_DEFAULT=30

# jota_voice_plist <python> <repo_dir> <config> <log_dir> <keepalive>
jota_voice_plist() {
    _jp_keep="$(jota_keepalive_xml "$5")" || return 1
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.jota.voice</string>
    <key>ProgramArguments</key>
    <array>
        <string>$1</string>
        <string>$2/client/app/voice_client.py</string>
        <string>$3</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key>${_jp_keep}
    <key>ThrottleInterval</key><integer>${JOTA_THROTTLE_INTERVAL:-$JOTA_THROTTLE_INTERVAL_DEFAULT}</integer>
    <key>ExitTimeOut</key><integer>${JOTA_EXIT_TIMEOUT:-$JOTA_EXIT_TIMEOUT_DEFAULT}</integer>
    <key>ProcessType</key><string>Interactive</string>
    <key>StandardOutPath</key><string>$4/stdout.log</string>
    <key>StandardErrorPath</key><string>$4/stderr.log</string>
    <key>WorkingDirectory</key><string>$2</string>
</dict></plist>
EOF
}

# jota_logrotate_plist <repo_dir> <interval_s> <log_dir>
# Agente que ejecuta rotate-logs.sh periódicamente (sin sudo: newsyslog.d
# exige root).
jota_logrotate_plist() {
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.jota-voice.logrotate</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/sh</string>
        <string>$1/install/macos/rotate-logs.sh</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>StartInterval</key><integer>$2</integer>
    <key>ProcessType</key><string>Background</string>
    <key>LowPriorityIO</key><true/>
    <key>StandardOutPath</key><string>$3/logrotate.log</string>
    <key>StandardErrorPath</key><string>$3/logrotate.log</string>
</dict></plist>
EOF
}
