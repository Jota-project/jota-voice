#!/bin/sh
set -e
source ../lib/output.sh
source ../lib/yaml.sh

_check() {
    [ -f "$HOME/supervisord.conf" ] && [ -f "$HOME/.jota-display-url" ]
}

_apply() {
    cp "$REPO_DIR/boot/supervisord.conf.tpl" "$HOME/supervisord.conf"
    _ok "supervisord.conf generado"

    local display_url
    display_url=$(yaml_get display.url || echo 'http://127.0.0.1:8766')
    echo "$display_url" > "$HOME/.jota-display-url"
    _ok "display URL guardada: $display_url"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "Configs ya copiadas" || exit 1
else
    _apply
fi