#!/bin/sh
# Crea symlink ~/Library/Application Support/jota-voice/config.yaml → devices/macbook_sito/config.yaml
set -e
. "$(dirname "$0")/00-lib.sh"

DEST_DIR="$HOME/Library/Application Support/jota-voice"
DEST="$DEST_DIR/config.yaml"
SRC="$REPO_DIR/devices/${DEVICE_ID}/config.yaml"

if [ ! -f "$SRC" ]; then
    _err "No existe $SRC. Crea devices/${DEVICE_ID}/config.yaml primero."
    exit 1
fi

mkdir -p "$DEST_DIR"
ln -sf "$SRC" "$DEST"
_ok "Symlink creado: $DEST → $SRC"