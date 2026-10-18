#!/bin/sh
# shellcheck disable=SC2015  # ok/bad siempre devuelven 0
# Test de los LaunchAgents de macOS (issue #46): KeepAlive solo tras fallo,
# ThrottleInterval y ExitTimeOut explícitos, plists válidos, y rotación de
# logs copy-truncate.
#
# La validación de plists necesita plutil (macOS); en Linux se salta esa
# parte y solo se prueba rotate-logs.sh.
#
# Uso: sh install/shared/tests/test_launchd.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
. "$ROOT/install/macos/launchd-plist.sh"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

fail=0
ok()  { echo "ok: $1"; }
bad() { echo "FAIL: $1"; fail=1; }

# --- plists ---------------------------------------------------------------
if command -v plutil >/dev/null 2>&1; then
    extract() { plutil -extract "$2" raw -o - "$1" 2>/dev/null; }

    jota_voice_plist /v/bin/python3 /repo "/cfg dir/config.yaml" /logs on-failure > "$TMP/v.plist"
    plutil -lint -s "$TMP/v.plist" && ok "plist cliente válido" || bad "plist cliente inválido"
    [ "$(extract "$TMP/v.plist" KeepAlive.SuccessfulExit)" = "false" ] \
        && ok "on-failure → SuccessfulExit=false" || bad "on-failure sin SuccessfulExit=false"
    [ "$(extract "$TMP/v.plist" ThrottleInterval)" = "30" ] && ok "ThrottleInterval=30" || bad "ThrottleInterval"
    [ "$(extract "$TMP/v.plist" ExitTimeOut)" = "30" ] && ok "ExitTimeOut=30" || bad "ExitTimeOut"

    # Valor antiguo true = on-failure (la intención era reinicio tras crash).
    jota_voice_plist p r c l true > "$TMP/t.plist"
    [ "$(extract "$TMP/t.plist" KeepAlive.SuccessfulExit)" = "false" ] && ok "true → on-failure" || bad "true"

    jota_voice_plist p r c l always > "$TMP/a.plist"
    [ "$(extract "$TMP/a.plist" KeepAlive)" = "true" ] && ok "always → KeepAlive=true" || bad "always"

    (JOTA_THROTTLE_INTERVAL=60 jota_voice_plist p r c l never) > "$TMP/n.plist"
    [ "$(extract "$TMP/n.plist" KeepAlive)" = "false" ] && ok "never → KeepAlive=false" || bad "never"
    [ "$(extract "$TMP/n.plist" ThrottleInterval)" = "60" ] && ok "JOTA_THROTTLE_INTERVAL respetado" || bad "throttle override"

    if jota_voice_plist p r c l nonsense > /dev/null 2>&1; then bad "modo inválido aceptado"; else ok "modo inválido rechazado"; fi

    jota_logrotate_plist /repo 3600 /logs > "$TMP/r.plist"
    plutil -lint -s "$TMP/r.plist" && ok "plist logrotate válido" || bad "plist logrotate inválido"
    [ "$(extract "$TMP/r.plist" StartInterval)" = "3600" ] && ok "logrotate cada hora" || bad "StartInterval"

    sed -e "s|__VENV_DIR__|/v|g" -e "s|__DATA_DIR__|/d|g" -e "s|__THRESHOLD__|0.4|g" -e "s|__HOME__|/h|g" \
        "$ROOT/install/macos/launchagent/com.jota-voice.wyoming-oww.plist" > "$TMP/o.plist"
    plutil -lint -s "$TMP/o.plist" && ok "plist OWW válido" || bad "plist OWW inválido"
    [ "$(extract "$TMP/o.plist" KeepAlive.SuccessfulExit)" = "false" ] && ok "OWW SuccessfulExit=false" || bad "OWW KeepAlive"
    [ -n "$(extract "$TMP/o.plist" ThrottleInterval)" ] && ok "OWW ThrottleInterval" || bad "OWW ThrottleInterval"
else
    echo "skip: sin plutil (no es macOS), no se validan los plists"
fi

# --- OWW solo en loopback (macOS y Termux) ---------------------------------
if grep -q 'tcp://127.0.0.1:10401' "$ROOT/install/macos/launchagent/com.jota-voice.wyoming-oww.plist" \
   && grep -q 'tcp://127.0.0.1:10401' "$ROOT/boot/supervisord.conf.tpl" \
   && ! grep -q '0\.0\.0\.0:10401' "$ROOT/install/macos/launchagent/com.jota-voice.wyoming-oww.plist" "$ROOT/boot/supervisord.conf.tpl"; then
    ok "OWW escucha en 127.0.0.1 en las dos plataformas"
else
    bad "OWW expuesto fuera de loopback"
fi

# --- rotate-logs.sh ---------------------------------------------------------
LOG="$TMP/stderr.log"
head -c 3000 /dev/zero | tr '\0' 'x' > "$LOG"
echo "viejo1" > "$LOG.1"
echo "viejo2" > "$LOG.2"
JOTA_LOG_MAX_KB=1 JOTA_LOG_KEEP=2 sh "$ROOT/install/macos/rotate-logs.sh" "$LOG" > /dev/null
[ ! -s "$LOG" ] && [ -f "$LOG" ] && ok "log truncado (sigue existiendo)" || bad "log no truncado"
[ "$(wc -c < "$LOG.1" | tr -d ' ')" = "3000" ] && ok "copia en .1" || bad "copia .1"
[ "$(cat "$LOG.2")" = "viejo1" ] && ok ".1 → .2" || bad "desplazamiento"
[ ! -f "$LOG.3" ] && ok "respeta JOTA_LOG_KEEP" || bad "KEEP"

echo "poco" > "$TMP/small.log"
JOTA_LOG_MAX_KB=1 sh "$ROOT/install/macos/rotate-logs.sh" "$TMP/small.log" > /dev/null
[ ! -f "$TMP/small.log.1" ] && ok "no rota por debajo del límite" || bad "rotó un log pequeño"

exit $fail
