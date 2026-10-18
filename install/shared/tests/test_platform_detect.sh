#!/bin/sh
# Test de jota_detect_platform (lib/platform.sh) y del dispatcher install.sh.
#
# Mismo orden que client/core/platform_key.py: darwin > PREFIX termux >
# hosts de Termux > linux > windows > unknown.
#
# Uso: sh install/shared/tests/test_platform_detect.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
. "$ROOT/lib/platform.sh"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
NO_HOSTS="$TMP/no-existe"
HOSTS="$TMP/hosts"
: > "$HOSTS"

fail=0
check() {
    # check <esperado> <uname> <PREFIX> <hosts_path>
    exp="$1"
    got=$(JOTA_UNAME_S="$2" PREFIX="$3" JOTA_TERMUX_HOSTS_PATH="$4" jota_detect_platform)
    if [ "$got" = "$exp" ]; then
        echo "ok: uname=$2 PREFIX=$3 → $got"
    else
        echo "FAIL: uname=$2 PREFIX=$3 hosts=$4 → '$got' (esperado '$exp')"
        fail=1
    fi
}

check darwin  Darwin ""                                  "$NO_HOSTS"
check darwin  Darwin "/data/data/com.termux/files/usr"   "$HOSTS"     # darwin gana
check termux  Linux  "/data/data/com.termux/files/usr"   "$NO_HOSTS"
check termux  Linux  ""                                  "$HOSTS"     # PREFIX saneado
check linux   Linux  "/usr"                              "$NO_HOSTS"
check windows MINGW64_NT-10.0 ""                         "$NO_HOSTS"
check unknown SunOS  ""                                  "$NO_HOSTS"

# El dispatcher respeta JOTA_PLATFORM y --detect no instala nada.
got=$(JOTA_PLATFORM=linux sh "$ROOT/install.sh" --detect)
if [ "$got" = "linux" ]; then
    echo "ok: install.sh --detect"
else
    echo "FAIL: install.sh --detect → '$got'"
    fail=1
fi

# Linux: mensaje claro y código 2, sin ejecutar nada.
out=$(JOTA_PLATFORM=linux sh "$ROOT/install.sh" 2>&1); rc=$?
if [ "$rc" -eq 2 ] && printf '%s' "$out" | grep -q "#54"; then
    echo "ok: linux → mensaje claro (rc=2)"
else
    echo "FAIL: linux → rc=$rc, salida: $out"
    fail=1
fi

exit $fail
