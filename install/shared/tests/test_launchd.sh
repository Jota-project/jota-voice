#!/bin/sh
# shellcheck disable=SC2015  # ok/bad siempre devuelven 0
# Test de los LaunchAgents de macOS (issue #46): KeepAlive solo tras fallo,
# ThrottleInterval y ExitTimeOut explícitos, plists válidos, y rotación de
# logs copy-truncate.
#
# Los plists se validan con plutil (macOS) o, si falta, con plistlib de
# Python (Linux/CI): ya no se salta la validación fuera de macOS.
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
# plutil en macOS; en Linux (CI) la misma validación con plistlib de Python.
PLIST_PY='
import plistlib, sys
d = plistlib.load(open(sys.argv[2], "rb"))
if sys.argv[1] == "extract":
    for k in sys.argv[3].split("."):
        d = d[int(k)] if isinstance(d, list) else d[k]
    print(str(d).lower() if isinstance(d, bool) else d)
'
if command -v plutil >/dev/null 2>&1; then
    lint()    { plutil -lint -s "$1"; }
    extract() { plutil -extract "$2" raw -o - "$1" 2>/dev/null; }
elif command -v python3 >/dev/null 2>&1; then
    echo "info: sin plutil, se validan los plists con plistlib"
    lint()    { python3 -c "$PLIST_PY" lint "$1" 2>/dev/null; }
    extract() { python3 -c "$PLIST_PY" extract "$1" "$2" 2>/dev/null; }
fi
if command -v lint >/dev/null 2>&1; then

    jota_voice_plist /v/bin/python3 /repo "/cfg dir/config.yaml" /logs on-failure > "$TMP/v.plist"
    lint "$TMP/v.plist" && ok "plist cliente válido" || bad "plist cliente inválido"
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
    if (JOTA_THROTTLE_INTERVAL=30s jota_voice_plist p r c l on-failure) > /dev/null 2>&1; then bad "throttle no entero aceptado"; else ok "throttle no entero rechazado"; fi
    if (JOTA_EXIT_TIMEOUT=-5 jota_voice_plist p r c l on-failure) > /dev/null 2>&1; then bad "exit timeout negativo aceptado"; else ok "exit timeout no entero rechazado"; fi
    if jota_oww_plist "$ROOT/install/macos/launchagent/com.jota-voice.wyoming-oww.plist" /v /d "0.4;x" /h > /dev/null 2>&1; then bad "threshold inválido aceptado"; else ok "threshold inválido rechazado"; fi

    # Rutas con caracteres especiales de XML y de sed: plist válido y valor
    # literal al leerlo (antes un "&" o "<" rompía el plist).
    WEIRD='/Users/x/Proyectos & <pruebas> "a" | b\c'
    jota_voice_plist "$WEIRD/py" "$WEIRD" "$WEIRD/cfg.yaml" "$WEIRD/logs" on-failure > "$TMP/w.plist"
    lint "$TMP/w.plist" && ok "rutas raras: plist válido" || bad "rutas raras: plist inválido"
    [ "$(extract "$TMP/w.plist" WorkingDirectory)" = "$WEIRD" ] && ok "rutas raras: valor literal" || bad "rutas raras: '$(extract "$TMP/w.plist" WorkingDirectory)'"
    jota_oww_plist "$ROOT/install/macos/launchagent/com.jota-voice.wyoming-oww.plist" "$WEIRD/venv" /d 0.4 "$WEIRD" > "$TMP/ow.plist"
    lint "$TMP/ow.plist" && ok "OWW rutas raras: plist válido" || bad "OWW rutas raras: plist inválido"
    [ "$(extract "$TMP/ow.plist" ProgramArguments.0)" = "$WEIRD/venv/bin/python" ] && ok "OWW rutas raras: valor literal" \
        || bad "OWW rutas raras: '$(extract "$TMP/ow.plist" ProgramArguments.0)'"

    jota_logrotate_plist /repo 3600 /logs > "$TMP/r.plist"
    lint "$TMP/r.plist" && ok "plist logrotate válido" || bad "plist logrotate inválido"
    [ "$(extract "$TMP/r.plist" StartInterval)" = "3600" ] && ok "logrotate cada hora" || bad "StartInterval"

    jota_oww_plist "$ROOT/install/macos/launchagent/com.jota-voice.wyoming-oww.plist" /v /d 0.4 /h > "$TMP/o.plist"
    lint "$TMP/o.plist" && ok "plist OWW válido" || bad "plist OWW inválido"
    [ "$(extract "$TMP/o.plist" KeepAlive.SuccessfulExit)" = "false" ] && ok "OWW SuccessfulExit=false" || bad "OWW KeepAlive"
    [ -n "$(extract "$TMP/o.plist" ThrottleInterval)" ] && ok "OWW ThrottleInterval" || bad "OWW ThrottleInterval"
else
    bad "sin plutil ni python3: no se pueden validar los plists"
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
