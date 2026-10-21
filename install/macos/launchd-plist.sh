#!/bin/sh
# launchd-plist.sh — Generadores de los plist de los LaunchAgents (issue #46).
# Funciones puras (escriben el plist en stdout) para poder validarlas con
# plutil en los tests sin tocar launchd.

# jota_xml_escape TEXTO — TEXTO apto para un <string> de plist: una ruta con
# "&" o "<" (p.ej. "~/Proyectos & pruebas") generaba un plist inválido.
jota_xml_escape() {
    printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' \
        -e 's/"/\&quot;/g' -e "s/'/\&apos;/g"
}

# jota_require_uint NOMBRE VALOR — falla si VALOR no es un entero >= 0
# (JOTA_THROTTLE_INTERVAL=30s dejaba un <integer> que launchd rechaza).
jota_require_uint() {
    case "$2" in
        ""|*[!0-9]*) echo "$1 debe ser un entero de segundos (es '$2')" >&2; return 1 ;;
    esac
}

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
    _jp_throttle="${JOTA_THROTTLE_INTERVAL:-$JOTA_THROTTLE_INTERVAL_DEFAULT}"
    _jp_exit="${JOTA_EXIT_TIMEOUT:-$JOTA_EXIT_TIMEOUT_DEFAULT}"
    jota_require_uint JOTA_THROTTLE_INTERVAL "$_jp_throttle" || return 1
    jota_require_uint JOTA_EXIT_TIMEOUT "$_jp_exit" || return 1
    _jp_py="$(jota_xml_escape "$1")"
    _jp_repo="$(jota_xml_escape "$2")"
    _jp_cfg="$(jota_xml_escape "$3")"
    _jp_log="$(jota_xml_escape "$4")"
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.jota.voice</string>
    <key>ProgramArguments</key>
    <array>
        <string>${_jp_py}</string>
        <string>${_jp_repo}/client/app/voice_client.py</string>
        <string>${_jp_cfg}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key>${_jp_keep}
    <key>ThrottleInterval</key><integer>${_jp_throttle}</integer>
    <key>ExitTimeOut</key><integer>${_jp_exit}</integer>
    <key>ProcessType</key><string>Interactive</string>
    <key>StandardOutPath</key><string>${_jp_log}/stdout.log</string>
    <key>StandardErrorPath</key><string>${_jp_log}/stderr.log</string>
    <key>WorkingDirectory</key><string>${_jp_repo}</string>
</dict></plist>
EOF
}

# jota_logrotate_plist <repo_dir> <interval_s> <log_dir>
# Agente que ejecuta rotate-logs.sh periódicamente (sin sudo: newsyslog.d
# exige root).
jota_logrotate_plist() {
    jota_require_uint StartInterval "$2" || return 1
    _jl_repo="$(jota_xml_escape "$1")"
    _jl_log="$(jota_xml_escape "$3")"
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.jota-voice.logrotate</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/sh</string>
        <string>${_jl_repo}/install/macos/rotate-logs.sh</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>StartInterval</key><integer>$2</integer>
    <key>ProcessType</key><string>Background</string>
    <key>LowPriorityIO</key><true/>
    <key>StandardOutPath</key><string>${_jl_log}/logrotate.log</string>
    <key>StandardErrorPath</key><string>${_jl_log}/logrotate.log</string>
</dict></plist>
EOF
}

# jota_oww_plist <plantilla> <venv> <data_dir> <threshold> <home>
# Rellena la plantilla del LaunchAgent de OWW escapando para XML y para la
# sustitución de sed ("\\", "&" y el separador "|").
jota_oww_plist() {
    case "$4" in
        ""|*[!0-9.]*) echo "OWW_THRESHOLD debe ser un número (es '$4')" >&2; return 1 ;;
    esac
    _jo_sed() { jota_xml_escape "$1" | sed -e 's/[\\&|]/\\&/g'; }
    sed \
        -e "s|__VENV_DIR__|$(_jo_sed "$2")|g" \
        -e "s|__DATA_DIR__|$(_jo_sed "$3")|g" \
        -e "s|__THRESHOLD__|$4|g" \
        -e "s|__HOME__|$(_jo_sed "$5")|g" \
        "$1"
}
