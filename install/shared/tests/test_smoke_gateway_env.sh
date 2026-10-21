#!/bin/sh
# Test de la resolución del gateway en install/shared/99-smoke-test.sh
# (issue #53): GATEWAY_HOST/GATEWAY_PORT del entorno tienen prioridad sobre
# el config. Usa JOTA_SMOKE_RESOLVE_ONLY=1 (no comprueba red ni servicios).
#
# Uso: sh install/shared/tests/test_smoke_gateway_env.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../99-smoke-test.sh"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/url.yaml" <<'EOF'
gateway:
  url: "wss://gateway.example.invalid/ws"
  client_key: "k"
EOF
cat > "$TMP/hostport.yaml" <<'EOF'
gateway:
  host: "lan.example.invalid"
  port: 8004
  client_key: "k"
EOF

fail=0
check() {
    # check <descripción> <esperado> <config> [VAR=valor...]
    desc="$1" exp="$2" cfg="$3"
    shift 3
    got=$(env -u GATEWAY_URL -u GATEWAY_HOST -u GATEWAY_PORT -u GW_HOST -u GW_PORT \
        "$@" CONFIG="$cfg" JOTA_SMOKE_RESOLVE_ONLY=1 sh "$SCRIPT" 2>&1 | tail -1)
    case "$got" in
        "$exp"*) echo "ok: $desc" ;;
        *) echo "FAIL: $desc → '$got' (esperado '$exp…')"; fail=1 ;;
    esac
}

check "config url"              "url=wss://gateway.example.invalid/ws host= port="  "$TMP/url.yaml"
check "config host+port"        "url= host=lan.example.invalid port=8004"          "$TMP/hostport.yaml"
check "GATEWAY_PORT gana"       "url= host=lan.example.invalid port=9999"          "$TMP/hostport.yaml" GATEWAY_PORT=9999
check "GATEWAY_HOST gana"       "url= host=otro.invalid port=8004"                 "$TMP/hostport.yaml" GATEWAY_HOST=otro.invalid
check "GATEWAY_HOST sobre url"  "url= host=otro.invalid port=81"                   "$TMP/url.yaml" GATEWAY_HOST=otro.invalid GATEWAY_PORT=81
check "GATEWAY_URL gana a todo" "url=ws://env.invalid:1 "                          "$TMP/hostport.yaml" GATEWAY_URL=ws://env.invalid:1 GATEWAY_HOST=x
check "GW_HOST (compat)"        "url= host=compat.invalid port=8004"               "$TMP/hostport.yaml" GW_HOST=compat.invalid

exit $fail
