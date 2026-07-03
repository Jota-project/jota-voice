#!/bin/sh
# Helpers compartidos para los scripts de macOS.
set -e

_ok() { printf "  \033[32m✓\033[0m %s\n" "$1"; }
_err() { printf "  \033[31m✗\033[0m %s\n" "$1" >&2; }
_warn() { printf "  \033[33m!\033[0m %s\n" "$1"; }
_info() { printf "  \033[36m→\033[0m %s\n" "$1"; }

REPO_DIR="${REPO_DIR:-$HOME/Work/jota-voice}"
VENV_DIR="${VENV_DIR:-$HOME/venvs/jota-voice}"
DEVICE_ID="${DEVICE_ID:-macbook_sito}"
