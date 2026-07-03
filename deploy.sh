#!/usr/bin/env bash
# deploy.sh — despliega jota-voice al target indicado (phone | macbook)
#
# Uso: ./deploy.sh [phone|macbook]
#
# phone   -> rsync al teléfono Android vía Termux (ssh) y reinicia el servicio.
# macbook -> rsync local a $HOME/Work/jota-voice y reinicia el launchd service.

set -euo pipefail

TARGET="${1:-phone}"

case "$TARGET" in
    macbook)
        REPO_LOCAL="${REPO_LOCAL:-$HOME/Work/jota-voice}"
        DEVICE_DIR="$REPO_LOCAL/devices/macbook_sito"

        if [ ! -d "$DEVICE_DIR" ]; then
            echo "ERROR: $DEVICE_DIR no existe. Crea devices/macbook_sito/config.yaml primero."
            exit 1
        fi

        echo "[deploy macbook] rsync local en $REPO_LOCAL…"
        rsync -a --delete \
            --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
            --exclude 'devices/*/.env' \
            ./ "$REPO_LOCAL/"

        # Symlink del config (install/macos/06-configs.sh lo crea también;
        # aquí solo aseguramos que existe tras el rsync).
        mkdir -p "$HOME/Library/Application Support/jota-voice"
        ln -sf "$DEVICE_DIR/config.yaml" "$HOME/Library/Application Support/jota-voice/config.yaml"

        echo "[deploy macbook] reiniciando launchd…"
        launchctl kickstart -k "gui/$(id -u)/com.jota.voice" || {
            echo "WARN: launchctl kickstart falló; el servicio puede no estar cargado todavía."
            echo "      Ejecuta install/macos/07-launchd.sh para activarlo."
        }
        echo "[deploy macbook] OK"
        ;;

    phone)
        # TODO: restaurar la lógica phone original (rsync vía sshpass al Huawei
        # vía Termux). El repo no tenía deploy.sh previamente; la lógica phone
        # previa vivía en scripts de shell ad-hoc. Si necesitas el flujo phone
        # exacto, revisa el historial de git antes de este refactor o el script
        # de bootstrap que arrancaba jota-watchdog v2.
        echo "[deploy phone] lógica phone pendiente de restaurar — usa los scripts ad-hoc previos"
        exit 1
        ;;

    *)
        echo "Uso: $0 [phone|macbook]"
        exit 1
        ;;
esac
