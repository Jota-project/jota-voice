#!/bin/sh
set -e
source "$REPO_DIR"/lib/output.sh
source "$REPO_DIR"/lib/yaml.sh

_check() {
    [ -f "$HOME/supervisord.conf" ] \
        && [ -f "$HOME/.jota-display-url" ] \
        && [ -x "$HOME/jota-voice/boot/sles-source-loader.sh" ] \
        && [ -f "$HOME/jota-voice/boot/lib/notify.sh" ]
}

_apply() {
    cp "$REPO_DIR/boot/supervisord.conf.tpl" "$HOME/supervisord.conf"
    _ok "supervisord.conf generado"

    local display_url
    display_url=$(yaml_get display.url || echo 'http://127.0.0.1:8766')
    echo "$display_url" > "$HOME/.jota-display-url"
    _ok "display URL guardada: $display_url"

    # sles-source-loader — dueño del micrófono, lo carga y monitoriza
    mkdir -p "$HOME/jota-voice/boot/lib"
    cp "$REPO_DIR/boot/sles-source-loader.sh" "$HOME/jota-voice/boot/sles-source-loader.sh"
    chmod +x "$HOME/jota-voice/boot/sles-source-loader.sh"
    _ok "sles-source-loader instalado"

    # notify.sh — librería compartida de notificaciones Termux
    cp "$REPO_DIR/boot/lib/notify.sh" "$HOME/jota-voice/boot/lib/notify.sh"
    chmod +x "$HOME/jota-voice/boot/lib/notify.sh"
    _ok "notify.sh instalado"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "Configs ya copiadas" || exit 1
else
    _apply
fi