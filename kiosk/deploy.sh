#!/usr/bin/env bash
# deploy.sh — Despliega el kiosk al teléfono vía SSH
# Uso: ./deploy.sh [device]
set -e

REPO_DIR="$(cd "$(dirname "$0")"/.. && pwd)"
DEVICE="${1:-}"

if [ -z "$DEVICE" ]; then
    files=$(find "$REPO_DIR/devices" -maxdepth 1 -name "*.env" -not -name "example.env" 2>/dev/null)
    count=$(echo "$files" | grep -c "^" || true)
    if [ "$count" -eq 0 ]; then
        echo "No hay dispositivos. Crea devices/<nombre>.env"
        exit 1
    fi
    DEVICE=$(basename "$files" .env)
fi

DEVICE_FILE="$REPO_DIR/devices/${DEVICE}.env"
if [ ! -f "$DEVICE_FILE" ]; then
    echo "Dispositivo '$DEVICE' no encontrado"
    exit 1
fi

source "$DEVICE_FILE"

SSH="sshpass -p $PHONE_PASS ssh -o StrictHostKeyChecking=no -p $PHONE_PORT $PHONE_HOST"
SCP="sshpass -p $PHONE_PASS scp -o StrictHostKeyChecking=no -P $PHONE_PORT"

echo "→ Desplegando kiosk a $DEVICE ($PHONE_HOST)..."
$SSH "mkdir -p $PHONE_DIR/kiosk/hooks"

echo "→ Copiando server.py, index.html, manifest.json..."
$SCP "$REPO_DIR/kiosk/server.py" "$PHONE_HOST:$PHONE_DIR/kiosk/server.py"
$SCP "$REPO_DIR/kiosk/index.html" "$PHONE_HOST:$PHONE_DIR/kiosk/index.html"
$SCP "$REPO_DIR/kiosk/manifest.json" "$PHONE_HOST:$PHONE_DIR/kiosk/manifest.json"

echo "→ Copiando hooks..."
for hook in "$REPO_DIR/kiosk/hooks"/*.sh; do
    [ -f "$hook" ] || continue
    $SCP "$hook" "$PHONE_HOST:$PHONE_DIR/kiosk/hooks/"
done

echo "→ Haciendo ejecutables..."
$SSH "chmod +x $PHONE_DIR/kiosk/hooks/*.sh"

echo "✓ Deploy completo"