#!/bin/sh
# Helpers compartidos para los scripts de macOS.
set -e

_MACOS_LIB_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$_MACOS_LIB_DIR/../../lib/output.sh"
. "$_MACOS_LIB_DIR/../../lib/secure.sh"
. "$_MACOS_LIB_DIR/../../lib/python.sh"

REPO_DIR="${REPO_DIR:-$HOME/Work/jota-voice}"
VENV_DIR="${VENV_DIR:-$HOME/venvs/jota-voice}"
DEVICE_ID="${DEVICE_ID:-macbook_sito}"
OWW_DATA_DIR="${OWW_DATA_DIR:-$HOME/wyoming-data}"
OWW_VENV_DIR="${OWW_VENV_DIR:-$HOME/wyoming-oww-venv}"

# DEVICE_ID acaba en rutas (devices/$DEVICE_ID/...): issue #55.
jota_valid_id "$DEVICE_ID" || _fail "DEVICE_ID inválido: '$DEVICE_ID' (solo letras, dígitos, _ y -)"
