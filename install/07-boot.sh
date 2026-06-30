#!/bin/sh
set -e
source "$REPO_DIR"/lib/output.sh

_check() {
    [ -x "$HOME/.termux/boot/jota-voice" ] && [ -x "$HOME/boot/lib/android.sh" ]
}

_apply() {
    mkdir -p "$HOME/.termux/boot"
    cp -f "$REPO_DIR/boot/hook.sh" "$HOME/.termux/boot/jota-voice"
    chmod +x "$HOME/.termux/boot/jota-voice"

    mkdir -p "$HOME/boot/lib"
    cp -f "$REPO_DIR/boot/lib/android.sh" "$HOME/boot/lib/android.sh"
    chmod +x "$HOME/boot/lib/android.sh"

    _ok "Boot hook instalado"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "Boot hook ya instalado" || exit 1
else
    _apply
fi