#!/bin/sh
# Note: as of 2026-07-06 config.yaml en la raíz es un symlink hacia
# devices/<id>/config.yaml (nunca trackeado en git). Ver
# docs/superpowers/specs/2026-07-06-device-config-identity-design.md.
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

# Asegura que $REPO_DIR/config.yaml es un symlink hacia devices/<id>/config.yaml.
# - Ya es symlink → no-op.
# - Fichero real preexistente (teléfono ya desplegado, pre-migración) → lo
#   mueve a devices/<device.id>/config.yaml y crea el symlink.
# - No existe nada → autodetecta entre devices/*/config.yaml (o usa
#   $DEVICE_ID si está fijado) y crea el symlink.
_ensure_device_config_symlink() {
    local cfg_path="$REPO_DIR/config.yaml"

    if [ -L "$cfg_path" ]; then
        return 0
    fi

    if [ -f "$cfg_path" ]; then
        local existing_id
        existing_id=$(yaml_get_nested device id)
        if [ -z "$existing_id" ]; then
            _err "config.yaml existe pero no tiene device.id (¿config antiguo con phone.name?)."
            _info "Migra el campo manualmente (añade device: / id: \"...\") y reintenta."
            return 1
        fi
        mkdir -p "$REPO_DIR/devices/$existing_id"
        mv "$cfg_path" "$REPO_DIR/devices/$existing_id/config.yaml"
        ln -s "devices/$existing_id/config.yaml" "$cfg_path"
        _ok "config.yaml migrado a devices/$existing_id/config.yaml (symlink creado)"
        return 0
    fi

    if [ -n "${DEVICE_ID:-}" ]; then
        if [ ! -f "$REPO_DIR/devices/$DEVICE_ID/config.yaml" ]; then
            _err "DEVICE_ID=$DEVICE_ID pero no existe devices/$DEVICE_ID/config.yaml"
            return 1
        fi
        ln -s "devices/$DEVICE_ID/config.yaml" "$cfg_path"
        _ok "symlink creado: config.yaml → devices/$DEVICE_ID/config.yaml"
        return 0
    fi

    local count=0 match="" f
    for f in "$REPO_DIR"/devices/*/config.yaml; do
        [ -f "$f" ] || continue
        count=$((count + 1))
        match="$f"
    done

    if [ "$count" -eq 0 ]; then
        _err "No hay ningún devices/<id>/config.yaml."
        _info "Créalo con: cp config.example.yaml devices/<id>/config.yaml && editarlo"
        return 1
    elif [ "$count" -gt 1 ]; then
        _err "Hay varios devices/*/config.yaml. Define DEVICE_ID=<nombre> y reintenta."
        return 1
    fi

    local id
    id=$(basename "$(dirname "$match")")
    ln -s "devices/$id/config.yaml" "$cfg_path"
    _ok "symlink creado: config.yaml → devices/$id/config.yaml"
}

_check() {
    [ -e "$REPO_DIR/config.yaml" ] \
        && [ -f "$HOME/supervisord.conf" ] \
        && [ -f "$HOME/.jota-display-url" ] \
        && [ -x "$HOME/jota-voice/boot/sles-source-loader.sh" ] \
        && [ -f "$HOME/jota-voice/boot/lib/notify.sh" ]
}

_apply() {
    _ensure_device_config_symlink || exit 1

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