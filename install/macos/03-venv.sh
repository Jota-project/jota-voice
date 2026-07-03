#!/bin/sh
# Crea el venv ~/venvs/jota-voice e instala dependencias Python.
set -e
. "$(dirname "$0")/00-lib.sh"

_info "Creando venv en $VENV_DIR…"
mkdir -p "$(dirname "$VENV_DIR")"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    _ok "venv creado"
else
    _ok "venv ya existe"
fi

_info "Actualizando pip e instalando requirements…"
"$VENV_DIR/bin/pip" install --upgrade pip wheel setuptools
"$VENV_DIR/bin/pip" install -r "$REPO_DIR/client/requirements.txt"
"$VENV_DIR/bin/pip" install sounddevice numpy
_ok "Dependencias Python instaladas"
