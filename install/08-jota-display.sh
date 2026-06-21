#!/bin/sh
set -e
source "$REPO_DIR"/lib/output.sh

DISPLAY_DIR="$HOME/jota-display"
DISPLAY_REPO="https://github.com/SitoSt/jota-display.git"

_check() {
    [ -d "$DISPLAY_DIR/.git" ] && [ -f "$DISPLAY_DIR/server/server.py" ]
}

_apply() {
    if [ -d "$DISPLAY_DIR/.git" ]; then
        _info "Actualizando jota-display desde GitHub"
        git -C "$DISPLAY_DIR" pull --ff-only
    else
        _info "Clonando jota-display desde GitHub"
        git clone --depth 1 "$DISPLAY_REPO" "$DISPLAY_DIR"
    fi
    _info "Construyendo frontend (npm install + vite build)"
    npm --prefix "$DISPLAY_DIR" install --silent
    npm --prefix "$DISPLAY_DIR" run build --silent
    _ok "jota-display instalado"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "jota-display ya instalado" || exit 1
else
    _apply
fi
