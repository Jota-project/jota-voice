#!/bin/sh
set -e
source "$REPO_DIR"/lib/output.sh
source "$REPO_DIR"/lib/python.sh

VENV="${REPO_DIR}/.venv"

_check() {
    [ -d "$VENV" ] && [ -f "$VENV/bin/pip" ]
}

_apply() {
    # El cliente exige Python >= 3.11 (asyncio.Task.cancelling(), issue #45).
    jota_python_ok python || _fail "El python de Termux es $(jota_python_version python || echo '?'); hace falta >= 3.11 (pkg upgrade python)."
    if [ -f "$VENV/bin/pip" ] && ! jota_python_ok "$VENV/bin/python"; then
        _warn "venv con Python < 3.11: se recrea"
        rm -rf "$VENV"
    fi
    if [ ! -f "$VENV/bin/pip" ]; then
        [ -d "$VENV" ] && rm -rf "$VENV"
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