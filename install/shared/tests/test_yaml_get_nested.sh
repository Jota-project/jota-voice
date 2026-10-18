#!/bin/sh
# Test de yaml_get_nested() (lib/yaml.sh), con el que install/06-configs.sh
# lee display.backend y display.url en el teléfono.
#
# Carga lib/yaml.sh tal cual y lo ejecuta contra un config.yaml de prueba con
# secciones anidadas, comillas, comentarios inline y comentarios en columna 0
# dentro de una sección (estos cerraban la sección antes de tiempo).
#
# Uso: sh install/shared/tests/test_yaml_get_nested.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$HERE/../../.."
. "$REPO_DIR/lib/yaml.sh"

YAML_FILE=$(mktemp)
trap 'rm -f "$YAML_FILE"' EXIT

cat > "$YAML_FILE" <<'EOF'
gateway:
  url: "wss://example.invalid/ws"   # otra sección con la misma clave

display:
# Kiosk de jota-display: comentario en columna 0 dentro de la sección
  backend: "http"              # comentario inline
  # comentario indentado
#   url: "http://comentado:1"
  url: "http://127.0.0.1:8766" # solo http
  timeout_s: 2.0

logging:
  level: "INFO"
EOF

fail=0
check() {
    test_section="$1" test_field="$2" test_expected="$3"
    got=$(yaml_get_nested "$test_section" "$test_field")
    if [ "$got" != "$test_expected" ]; then
        echo "FAIL: yaml_get_nested $test_section $test_field = '$got' (esperado '$test_expected')"
        fail=1
    else
        echo "ok: yaml_get_nested $test_section $test_field = '$got'"
    fi
}

check display backend   "http"
check display url       "http://127.0.0.1:8766"
check display timeout_s "2.0"
check gateway url       "wss://example.invalid/ws"
check logging level     "INFO"
check display port      ""
check nada    url       ""

exit $fail
