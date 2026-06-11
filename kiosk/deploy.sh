#!/usr/bin/env bash
# Despliega el kiosk al teléfono vía SSH
set -e

PHONE="u0_a161@192.168.1.129"
PORT=8022
PASS="cacatua420"
DEST="~/kiosk"

SSH="sshpass -p $PASS ssh -p $PORT -o StrictHostKeyChecking=no $PHONE"
SCP="sshpass -p $PASS scp -P $PORT -o StrictHostKeyChecking=no"

echo "→ Creando directorios en el teléfono..."
$SSH "mkdir -p $DEST/hooks"

echo "→ Copiando server.py e index.html..."
$SCP "$(dirname "$0")/server.py"      "$PHONE:$DEST/server.py"
$SCP "$(dirname "$0")/index.html"     "$PHONE:$DEST/index.html"
$SCP "$(dirname "$0")/manifest.json"  "$PHONE:$DEST/manifest.json"

echo "→ Copiando hooks..."
$SCP "$(dirname "$0")/hooks/"*.sh  "$PHONE:$DEST/hooks/"

echo "→ Haciendo ejecutables los hooks..."
$SSH "chmod +x $DEST/hooks/*.sh"

echo "✓ Deploy completo."
