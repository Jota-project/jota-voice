#!/bin/sh
# Crea el venv ~/venvs/jota-voice e instala dependencias Python.
set -e
. "$(dirname "$0")/00-lib.sh"

# Intérprete explícito (python@3.12 de Homebrew), no el python3 del PATH
# (issue #45). El cliente exige >= 3.11.
PY="$(jota_find_python)" || exit 1
_info "Python: $PY ($(jota_python_version "$PY"))"

# Un venv previo creado con un Python < 3.11 (p.ej. el python3 de Xcode)
# no sirve: se recrea. Es regenerable, no guarda datos del usuario.
if [ -d "$VENV_DIR" ] && ! jota_python_ok "$VENV_DIR/bin/python"; then
    _warn "venv $VENV_DIR con Python $(jota_python_version "$VENV_DIR/bin/python" || echo '?') (< 3.11 o roto): se recrea"
    rm -rf "$VENV_DIR"
fi

_info "Creando venv en $VENV_DIR…"
mkdir -p "$(dirname "$VENV_DIR")"
if [ ! -d "$VENV_DIR" ]; then
    "$PY" -m venv "$VENV_DIR"
    _ok "venv creado"
else
    _ok "venv ya existe ($(jota_python_version "$VENV_DIR/bin/python"))"
fi

_info "Actualizando pip e instalando requirements…"
"$VENV_DIR/bin/pip" install --upgrade pip wheel setuptools
"$VENV_DIR/bin/pip" install -r "$REPO_DIR/client/requirements.txt"
_info "Instalando requirements específicos de macOS si existen…"
if [ -f "$REPO_DIR/requirements-macos.txt" ]; then
    "$VENV_DIR/bin/pip" install -r "$REPO_DIR/requirements-macos.txt"
fi
_ok "Dependencias Python instaladas"
