#!/bin/sh
set -e
source ../lib/output.sh

OWW_VENV="$HOME/oww-venv"
OWW_MODEL="$HOME/.local/lib/python3.*/site-packages/wyoming_openwakeword/models/ok_nabu.tflite"

_model_exists() {
    ls $OWW_MODEL 2>/dev/null | head -1 | grep -q "ok_nabu"
}

_check() {
    [ -d "$OWW_VENV" ] && _model_exists
}

_apply() {
    if [ ! -d "$OWW_VENV" ]; then
        _info "Creando oww-venv"
        python -m venv "$OWW_VENV"
    fi
    _info "Instalando wyoming-openwakeword"
    "$OWW_VENV/bin/pip" install -q wyoming-openwakeword
    _info "Descargando modelo ok_nabu"
    "$OWW_VENV/bin/python3" -c "
from wyoming_openwakeword.download import ensure_model_exists
ensure_model_exists('ok_nabu')
print('Modelo ok_nabu descargado')
"
    _ok "OWW instalado con modelo ok_nabu"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "OWW ya instalado" || exit 1
else
    _apply
fi