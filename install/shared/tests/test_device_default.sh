#!/bin/sh
# Test de jota_default_device_id (lib/device.sh) — issue #101: el id por
# defecto ya no es el de un dispositivo real hardcodeado.
#
# Uso: sh install/shared/tests/test_device_default.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
. "$ROOT/lib/device.sh"
. "$ROOT/lib/secure.sh"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

fail=0
check() {
    if [ "$2" = "$3" ]; then echo "ok: $1 → $2"; else echo "FAIL: $1 → '$2' (esperado '$3')"; fail=1; fi
}

mkdir -p "$TMP/uno/devices/mi-mac" "$TMP/dos/devices/a" "$TMP/dos/devices/b" "$TMP/cero/devices"
: > "$TMP/uno/devices/mi-mac/config.yaml"
: > "$TMP/dos/devices/a/config.yaml"
: > "$TMP/dos/devices/b/config.yaml"

check "DEVICE_ID manda"        "$(DEVICE_ID=fijo jota_default_device_id "$TMP/uno")" "fijo"
check "único config"           "$(unset DEVICE_ID; jota_default_device_id "$TMP/uno")" "mi-mac"
host_id="$(unset DEVICE_ID; jota_default_device_id "$TMP/cero")"
if jota_valid_id "$host_id"; then echo "ok: sin configs → hostname saneado ($host_id)"; else echo "FAIL: id de hostname inválido '$host_id'"; fail=1; fi
check "varios configs → hostname" "$(unset DEVICE_ID; jota_default_device_id "$TMP/dos")" "$host_id"

# Ningún script fija un id de dispositivo literal como default
# (antes: DEVICE_ID="${DEVICE_ID:-<id real>}").
if grep -rnE 'DEVICE_ID:-[A-Za-z0-9_]' "$ROOT/install" "$ROOT/lib" "$ROOT/deploy.sh" "$ROOT/install.sh" \
        --include='*.sh' --exclude='test_device_default.sh' 2>/dev/null; then
    echo "FAIL: queda un DEVICE_ID por defecto literal"; fail=1
else
    echo "ok: sin DEVICE_ID por defecto literal"
fi

exit $fail
