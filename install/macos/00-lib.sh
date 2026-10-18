#!/bin/sh
# Helpers compartidos para los scripts de macOS.
set -e

_MACOS_LIB_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$_MACOS_LIB_DIR/../../lib/output.sh"
. "$_MACOS_LIB_DIR/../../lib/secure.sh"
. "$_MACOS_LIB_DIR/../../lib/python.sh"
. "$_MACOS_LIB_DIR/../../lib/device.sh"

# Sin rutas ni ids del despliegue real (issue #101): el repo es el que
# contiene este script y el id sale de devices/ o del hostname.
REPO_DIR="${REPO_DIR:-$(cd "$_MACOS_LIB_DIR/../.." && pwd)}"
VENV_DIR="${VENV_DIR:-$HOME/venvs/jota-voice}"
DEVICE_ID="$(jota_default_device_id "$REPO_DIR")"
OWW_DATA_DIR="${OWW_DATA_DIR:-$HOME/wyoming-data}"
OWW_VENV_DIR="${OWW_VENV_DIR:-$HOME/wyoming-oww-venv}"

# DEVICE_ID acaba en rutas (devices/$DEVICE_ID/...): issue #55.
jota_valid_id "$DEVICE_ID" || _fail "DEVICE_ID inválido: '$DEVICE_ID' (solo letras, dígitos, _ y -)"
