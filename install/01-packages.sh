#!/bin/sh
set -e
source "$REPO_DIR"/lib/output.sh

PKGS="python python-numpy python-scipy python-tflite-runtime python-onnxruntime portaudio pulseaudio termux-api git openssh termux-tools ffmpeg rsync nodejs"

_need_pkg() {
    pkg list-installed 2>/dev/null | grep -q "^$1/" && return 1 || return 0
}

_check() {
    return 0
}

_apply() {
    local to_install=""
    for p in $PKGS; do
        _need_pkg "$p" && to_install="$to_install $p"
    done
    if [ -n "$to_install" ]; then
        _info "Instalando:$to_install"
        pkg install -y $to_install
    fi
    _ok "Paquetes actualizados"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check
else
    _apply
fi