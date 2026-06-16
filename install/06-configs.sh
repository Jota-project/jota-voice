#!/bin/sh
set -e
source ../lib/output.sh

_check() {
    [ -f "$HOME/supervisord.conf" ]
}

_apply() {
    cp "$REPO_DIR/boot/supervisord.conf.tpl" "$HOME/supervisord.conf"
    _ok "supervisord.conf generado"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "Configs ya copiadas" || exit 1
else
    _apply
fi