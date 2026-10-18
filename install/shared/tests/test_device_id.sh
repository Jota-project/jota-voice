#!/bin/sh
# Test de jota_valid_id (lib/secure.sh) y del wizard de macOS (issue #55):
# un device id con "/" o ".." no puede escapar de devices/.
#
# Uso: sh install/shared/tests/test_device_id.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
. "$ROOT/lib/secure.sh"

fail=0
for id in macbook mi-movil tel_2 A1; do
    if jota_valid_id "$id"; then echo "ok: válido '$id'"; else echo "FAIL: rechazado '$id'"; fail=1; fi
done
for id in "" "../../tmp/pwned" "a/b" ".." "-rf" "con espacio" 'x$(id)' "tab	x" \
          "$(printf 'a%.0s' $(seq 1 65))"; do
    if jota_valid_id "$id"; then echo "FAIL: aceptado '$id'"; fail=1; else echo "ok: rechazado '$id'"; fi
done

# Wizard: un id malicioso se vuelve a pedir; nada se crea fuera de devices/.
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/repo/devices"
printf '%s\n' "../../escape" "tel_ok" "" | \
    REPO_DIR="$TMP/repo" DEVICE_ID="defecto" GATEWAY_URL="wss://gw.example.invalid/ws" \
    GATEWAY_CLIENT_KEY="clave-de-prueba" OWW_DATA_DIR="$TMP/oww" \
    sh "$ROOT/install/macos/02-config-wizard.sh" > "$TMP/out" 2>&1
if [ -f "$TMP/repo/devices/tel_ok/config.yaml" ]; then echo "ok: wizard reintenta y crea devices/tel_ok"; else echo "FAIL: wizard: $(cat "$TMP/out")"; fail=1; fi
if [ -e "$TMP/escape" ] || [ -e "$TMP/repo/escape" ]; then echo "FAIL: el wizard escapó de devices/"; fail=1; else echo "ok: nada fuera de devices/"; fi
mode=$(_jota_file_mode "$TMP/repo/devices/tel_ok/config.yaml")
if [ "$mode" = "600" ]; then echo "ok: config 600"; else echo "FAIL: config $mode"; fail=1; fi

# Comillas en el client_key → rechazo (rompería el YAML).
printf '%s\n' "tel_q" | \
    REPO_DIR="$TMP/repo" DEVICE_ID="defecto" GATEWAY_URL="wss://gw.example.invalid/ws" \
    GATEWAY_CLIENT_KEY='ab"c' OWW_DATA_DIR="$TMP/oww" \
    sh "$ROOT/install/macos/02-config-wizard.sh" > "$TMP/out" 2>&1
if [ ! -f "$TMP/repo/devices/tel_q/config.yaml" ]; then echo "ok: client_key con comillas rechazado"; else echo "FAIL: aceptó comillas"; fail=1; fi

exit $fail
