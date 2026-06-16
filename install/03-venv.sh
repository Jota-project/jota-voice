#!/bin/sh
set -e
source ../lib/output.sh

VENV="${REPO_DIR}/.venv"

_check() {
    [ -d "$VENV" ] && [ -f "$VENV/bin/pip" ]
}

_apply() {
    if [ ! -d "$VENV" ]; then
        _info "Creando venv con --system-site-packages"
        python -m venv --system-site-packages "$VENV"
    fi
    _info "Actualizando dependencias pip"
    "$VENV/bin/pip" install -q -r "$REPO_DIR/client/requirements.txt" --upgrade
    _ok "venv listo"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "venv ya existe" || exit 1
else
    _apply
fi