#!/bin/sh
# Test de jota_secure_perms (lib/secure.sh) — issues #44 y #56: los ficheros
# de secretos de devices/ vuelven a 600 en cada ejecución, aunque ya existan.
#
# Uso: sh install/shared/tests/test_secure_perms.sh
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
. "$ROOT/lib/secure.sh"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/devices/mac" "$TMP/devices/tel"
for f in devices/mac/config.yaml devices/mac/.env devices/tel/config.yaml devices/tel.env devices/example.env; do
    echo "x: y" > "$TMP/$f"
    chmod 644 "$TMP/$f"
done
ln -s devices/mac/config.yaml "$TMP/config.yaml"

fail=0
out=$(jota_secure_perms "$TMP")

for f in devices/mac/config.yaml devices/mac/.env devices/tel/config.yaml devices/tel.env; do
    m=$(_jota_file_mode "$TMP/$f")
    if [ "$m" = "600" ]; then echo "ok: $f → 600"; else echo "FAIL: $f → $m"; fail=1; fi
done
m=$(_jota_file_mode "$TMP/devices/example.env")
if [ "$m" = "644" ]; then echo "ok: example.env intacto"; else echo "FAIL: example.env → $m"; fail=1; fi
if printf '%s' "$out" | grep -q "644 → 600"; then echo "ok: informa de lo corregido"; else echo "FAIL: salida: $out"; fail=1; fi

# Segunda ejecución: nada que corregir, mensaje de verificado.
out=$(jota_secure_perms "$TMP")
if printf '%s' "$out" | grep -q "verificados"; then echo "ok: idempotente"; else echo "FAIL: 2ª ejecución: $out"; fail=1; fi

# Sin devices/ no falla (set -e activo en este test).
mkdir -p "$TMP/vacio"
jota_secure_perms "$TMP/vacio" >/dev/null && echo "ok: repo sin devices/"

exit $fail
