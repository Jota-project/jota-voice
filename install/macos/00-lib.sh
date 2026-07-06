#!/bin/sh
# Helpers compartidos para los scripts de macOS.
set -e

_MACOS_LIB_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$_MACOS_LIB_DIR/../../lib/output.sh"

REPO_DIR="${REPO_DIR:-$HOME/Work/jota-voice}"
VENV_DIR="${VENV_DIR:-$HOME/venvs/jota-voice}"
DEVICE_ID="${DEVICE_ID:-macbook_sito}"
OWW_DATA_DIR="${OWW_DATA_DIR:-$HOME/wyoming-data}"
