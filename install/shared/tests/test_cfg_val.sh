#!/bin/sh
# Test de _cfg_val() — el extractor YAML de install/shared/99-smoke-test.sh.
#
# Extrae la función TAL CUAL está en el script real (no una copia que pueda
# divergir) y la ejecuta contra un config.yaml de prueba con la misma forma
# que los reales del repo: secciones anidadas, valores entrecomillados y
# comentarios inline.
#
# Uso: sh install/shared/tests/test_cfg_val.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../99-smoke-test.sh"

CONFIG=$(mktemp)
trap 'rm -f "$CONFIG"' EXIT

cat > "$CONFIG" <<'EOF'
gateway:
  url: "wss://green-house.alfonsogarre.com/api/gateway/ws/stream"
  client_key: "test-key"
  connect_timeout_s: 10

device:
  id: "test-device"

oww:
  host: "127.0.0.1"            # Wyoming corre en Docker local en el Mac
  port: 10401
  wake_words:
    - "ok_nabu"
EOF

eval "$(awk '/_cfg_val\(\) \{/,/^    \}$/' "$SCRIPT")"

fail=0
check() {
    # Nombres distintos a los parámetros internos de _cfg_val (key/section/
    # field): mezclarlos enmascara bugs de aliasing entre el harness y la
    # función bajo test (nos pasó una vez escribiendo este mismo test).
    test_key="$1" test_expected="$2"
    got=$(_cfg_val "$test_key")
    if [ "$got" != "$test_expected" ]; then
        echo "FAIL: _cfg_val('$test_key') = '$got' (esperado '$test_expected')"
        fail=1
    else
        echo "ok: _cfg_val('$test_key') = '$got'"
    fi
}

check "gateway.url"   "wss://green-house.alfonsogarre.com/api/gateway/ws/stream"
check "oww.host"      "127.0.0.1"
check "oww.port"      "10401"
check "device.id"     "test-device"
check "gateway.host"  ""
check "no.existe"     ""

exit $fail
