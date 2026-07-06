#!/bin/sh
# install.sh — Runner único para setup de jota-voice en macOS.
# Encadena homebrew → config wizard → venv → oww → configs → launchd → smoke test.
set -u
REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
export REPO_DIR

_failed=0
for step in 01-homebrew.sh 02-config-wizard.sh 03-venv.sh 04-oww.sh 06-configs.sh 07-launchd.sh; do
    echo ""
    echo "=== $step ==="
    if ! sh "$REPO_DIR/install/macos/$step"; then
        echo "  ⚠ $step falló"
        _failed=1
        break
    fi
done

echo ""
if [ "$_failed" -eq 0 ]; then
    echo "✓ Instalación completada — ejecutando smoke test…"
    sh "$REPO_DIR/install/shared/99-smoke-test.sh" || true
else
    echo "✗ Instalación abortada — revisa el error arriba antes de continuar"
    exit 1
fi
