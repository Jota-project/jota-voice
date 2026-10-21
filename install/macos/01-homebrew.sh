#!/bin/sh
# Instala Homebrew (si falta), Python 3.12, PortAudio, sox y Docker.
set -e
. "$(dirname "$0")/00-lib.sh"

_info "Comprobando Homebrew…"
if ! command -v brew >/dev/null 2>&1; then
    _info "Instalando Homebrew…"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    _ok "Homebrew instalado"
else
    _ok "Homebrew ya presente: $(brew --version | head -1)"
fi

_info "Instalando python@3.12, portaudio, sox…"
brew install python@3.12 portaudio sox
_ok "Dependencias de sistema instaladas"
# python@3.12 es keg-only: los venvs se crean con esta ruta, no con el
# python3 del PATH (issue #45).
PY="$(jota_find_python)" || exit 1
_ok "Python para los venvs: $PY ($(jota_python_version "$PY"))"

_info "Comprobando Docker…"
if ! command -v docker >/dev/null 2>&1; then
    _warn "Docker no encontrado. Instala OrbStack (https://orbstack.dev) o Docker Desktop y vuelve a correr este script."
    exit 1
fi
_ok "Docker disponible: $(docker --version)"
