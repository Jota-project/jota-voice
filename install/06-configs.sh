#!/bin/sh
set -e
source "$REPO_DIR"/lib/output.sh
source "$REPO_DIR"/lib/yaml.sh

# Detecta si REPO_DIR == HOME/jota-voice (mismo sistema de ficheros, mismo path)
SAME_TREE=0
[ "$REPO_DIR" = "$HOME/jota-voice" ] && SAME_TREE=1

# Copia un fichero al destino, idempotente. Si origen y destino son el mismo
# path (caso SAME_TREE), solo asegura que existe y aplica permisos.
_install_file() {
    local src="$1" dst="$2" mode="${3:-0644}"
    if [ "$SAME_TREE" = 1 ] && [ "$src" = "$dst" ]; then
        [ -f "$dst" ] || { _err "Falta $dst"; return 1; }
        chmod "$mode" "$dst"
    else
        mkdir -p "$(dirname "$dst")"
        cp -f "$src" "$dst"
        chmod "$mode" "$dst"
    fi
}

_check() {
    [ -f "$HOME/supervisord.conf" ] \
        && [ -f "$HOME/.jota-display-url" ] \
        && [ -x "$HOME/jota-voice/boot/sles-source-loader.sh" ] \
        && [ -f "$HOME/jota-voice/boot/lib/notify.sh" ]
}

_apply() {
    _install_file "$REPO_DIR/boot/supervisord.conf.tpl" "$HOME/supervisord.conf" 0600
    _ok "supervisord.conf generado"

    local display_url
    display_url=$(yaml_get display.url 2>/dev/null)
    display_url="${display_url:-http://127.0.0.1:8766}"
    # Limpiar comentario inline si existe
    display_url="${display_url%%#*}"
    display_url=$(echo "$display_url" | xargs)
    echo "$display_url" > "$HOME/.jota-display-url"
    _ok "display URL guardada: $display_url"

    # sles-source-loader — dueño del micrófono, lo carga y monitoriza
    _install_file "$REPO_DIR/boot/sles-source-loader.sh" \
        "$HOME/jota-voice/boot/sles-source-loader.sh" 0755
    _ok "sles-source-loader instalado"

    # notify.sh — librería compartida de notificaciones Termux
    _install_file "$REPO_DIR/boot/lib/notify.sh" \
        "$HOME/jota-voice/boot/lib/notify.sh" 0755
    _ok "notify.sh instalado"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "Configs ya copiadas" || exit 1
else
    _apply
fi