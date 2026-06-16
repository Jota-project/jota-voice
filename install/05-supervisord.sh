#!/bin/sh
set -e
source ../lib/output.sh

_check() {
    command -v supervisord >/dev/null 2>&1
}

_apply() {
    _info "Instalando supervisor via pip"
    pip install -q supervisor
    _ok "supervisord instalado"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "supervisord ya instalado" || exit 1
else
    _apply
fi