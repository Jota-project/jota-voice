#!/data/data/com.termux/files/usr/bin/sh
# install-termux.sh — Runner idempotente para setup de jota-voice en Termux.
# Se invoca desde el install.sh raíz (punto de entrada único, issue #42).

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export REPO_DIR

source "$REPO_DIR/lib/output.sh"

# ── Validación ────────────────────────────────────────────────
if [ ! -f "$REPO_DIR/config.yaml" ]; then
    _fail "No existe config.yaml. Ejecuta 'jota-voice init' en el Mac."
fi

if grep -q "RELLENAR\|YOUR_" "$REPO_DIR/config.yaml" 2>/dev/null; then
    _fail "config.yaml tiene campos sin rellenar."
fi

_ok "Configuración válida"

# ── Ejecutar pasos ────────────────────────────────────────────
# Lista explícita (no un glob de install/*.sh): este runner y otros ficheros
# viven en install/ y no son pasos.
STEPS="01-packages.sh 02-hosts.sh 03-venv.sh 04-oww.sh 05-supervisord.sh 06-configs.sh 07-boot.sh 08-jota-display.sh 99-smoke-test.sh"

_failed=0
for name in $STEPS; do
    step="$REPO_DIR/install/$name"
    echo ""
    echo "=== $name ==="
    if ! . "$step"; then
        echo "  ⚠ $name falló (código: $?)"
        _failed=1
    fi
done

echo ""
if [ "$_failed" -eq 0 ]; then
    echo "✓ install.sh completado — todos los pasos exitosos"
else
    echo "⚠ install.sh completado con errores en algunos pasos"
fi
