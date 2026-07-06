#!/usr/bin/env bash
# deploy.sh — despliega jota-voice al target indicado (phone | macbook)
#
# Uso: ./deploy.sh phone [device_name]
#      ./deploy.sh macbook
#
# phone   -> rsync al teléfono Android vía Termux (ssh) y reinicia jota-voice
#            vía supervisorctl. Lee credenciales de devices/<device_name>.env
#            (ver devices/example.env). Si solo hay un devices/*.env, se
#            autodetecta.
# macbook -> rsync local a $HOME/Work/jota-voice y reinicia el launchd service.

set -euo pipefail

REPO_LOCAL_SELF="$(cd "$(dirname "$0")" && pwd)"
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
            --exclude 'devices/*/.env' --exclude 'devices/*/config.yaml' \
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
        DEVICES_DIR="$REPO_LOCAL_SELF/devices"
        DEVICE_NAME="${2:-}"

        if [ -z "$DEVICE_NAME" ]; then
            CANDIDATES=()
            for f in "$DEVICES_DIR"/*.env; do
                [ -f "$f" ] || continue
                [ "$(basename "$f")" = "example.env" ] && continue
                CANDIDATES+=("$f")
            done
            case "${#CANDIDATES[@]}" in
                0)
                    echo "ERROR: no hay ningún devices/*.env. Copia devices/example.env a devices/<nombre>.env y rellénalo."
                    exit 1
                    ;;
                1)
                    DEVICE_NAME="$(basename "${CANDIDATES[0]}" .env)"
                    ;;
                *)
                    echo "ERROR: hay varios dispositivos en devices/*.env. Especifica uno:"
                    echo "  ./deploy.sh phone <nombre>"
                    echo "Disponibles:"
                    for f in "${CANDIDATES[@]}"; do echo "  - $(basename "$f" .env)"; done
                    exit 1
                    ;;
            esac
        fi

        DEVICE_ENV="$DEVICES_DIR/${DEVICE_NAME}.env"
        if [ ! -f "$DEVICE_ENV" ]; then
            echo "ERROR: $DEVICE_ENV no existe."
            exit 1
        fi

        command -v sshpass >/dev/null 2>&1 || {
            echo "ERROR: falta sshpass (brew install sshpass / pkg install sshpass)."
            exit 1
        }

        set -a
        # shellcheck disable=SC1090
        . "$DEVICE_ENV"
        set +a

        for var in PHONE_HOST PHONE_PORT PHONE_USER PHONE_PASS PHONE_DIR; do
            eval val=\$$var
            if [ -z "$val" ]; then
                echo "ERROR: $var no definida en $DEVICE_ENV"
                exit 1
            fi
        done

        PHONE_TARGET="${PHONE_USER}@${PHONE_HOST}"

        _phone_ssh() {
            sshpass -p "$PHONE_PASS" ssh -p "$PHONE_PORT" "$PHONE_TARGET" "$@"
        }
        _phone_rsync() {
            sshpass -p "$PHONE_PASS" rsync -a --delete \
                -e "ssh -p $PHONE_PORT" \
                --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
                --exclude 'config.yaml' --exclude 'devices' \
                "$@"
        }

        echo "[deploy phone:${DEVICE_NAME}] sincronizando con ${PHONE_TARGET}:${PHONE_DIR}…"
        _phone_ssh "mkdir -p '$PHONE_DIR'"
        _phone_rsync "$REPO_LOCAL_SELF/" "${PHONE_TARGET}:${PHONE_DIR}/"

        echo "[deploy phone:${DEVICE_NAME}] reiniciando jota-voice vía supervisorctl…"
        _phone_ssh 'supervisorctl -c "$HOME/supervisord.conf" restart jota-voice'
        echo "[deploy phone:${DEVICE_NAME}] OK"
        ;;

    *)
        echo "Uso: $0 [phone|macbook]"
        exit 1
        ;;
esac
