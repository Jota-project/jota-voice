#!/data/data/com.termux/files/usr/bin/sh
# install.sh — Runner idempotente para setup de jota-voice en Termux
# Ejecuta cada paso en install/ si no está ya hecho

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

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
for step in install/*.sh; do
    name=$(basename "$step")
    echo ""
    echo "=== $name ==="
    . "$step"
done