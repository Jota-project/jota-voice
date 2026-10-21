#!/usr/bin/env bash
# deploy.sh — despliega jota-voice al target indicado (phone | macbook)
#
# Uso: ./deploy.sh phone [device_name]
#      REPO_LOCAL=<copia de despliegue> ./deploy.sh macbook
#
# phone   -> rsync al teléfono Android vía Termux (ssh) y reinicia jota-voice
#            vía supervisorctl. Lee la conexión de devices/<device_name>.env
#            (ver devices/example.env). Si solo hay un devices/*.env, se
#            autodetecta. Autenticación: clave SSH (recomendado, PHONE_PASS
#            vacío) o contraseña vía sshpass -e (nunca en la línea de órdenes).
# macbook -> rsync de ESTE checkout (el directorio del script, no el actual)
#            a $REPO_LOCAL, que es obligatorio, y reinicia el LaunchAgent.

set -euo pipefail

REPO_LOCAL_SELF="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-phone}"
# shellcheck source=lib/secure.sh
. "$REPO_LOCAL_SELF/lib/secure.sh"

case "$TARGET" in
    macbook)
        # Sin default: un rsync --delete hacia una ruta supuesta es peligroso.
        if [ -z "${REPO_LOCAL:-}" ]; then
            echo "ERROR: define REPO_LOCAL=<copia de despliegue> (la ruta desde la que corre el LaunchAgent)."
            exit 1
        fi
        mkdir -p "$REPO_LOCAL"
        REPO_LOCAL="$(cd "$REPO_LOCAL" && pwd)"
        case "$REPO_LOCAL" in
            /|"$HOME"|"$REPO_LOCAL_SELF")
                echo "ERROR: REPO_LOCAL=$REPO_LOCAL no es un destino válido (raíz, \$HOME o este mismo checkout)."
                exit 1
                ;;
        esac
        # Id del Mac: DEVICE_ID, o el único devices/<id>/config.yaml, o el
        # hostname (issue #101: sin ids reales en el código).
        # shellcheck source=lib/device.sh
        . "$REPO_LOCAL_SELF/lib/device.sh"
        MAC_ID="$(jota_default_device_id "$REPO_LOCAL")"
        # Copia de despliegue aún sin config propio: el id del checkout origen.
        if [ ! -f "$REPO_LOCAL/devices/$MAC_ID/config.yaml" ]; then
            MAC_ID="$(jota_default_device_id "$REPO_LOCAL_SELF")"
        fi
        if ! jota_valid_id "$MAC_ID"; then
            echo "ERROR: DEVICE_ID inválido: '$MAC_ID'"
            exit 1
        fi
        DEVICE_DIR="$REPO_LOCAL/devices/$MAC_ID"

        if [ ! -f "$DEVICE_DIR/config.yaml" ]; then
            echo "ERROR: $DEVICE_DIR/config.yaml no existe. Ejecuta install/macos/02-config-wizard.sh o define DEVICE_ID=<id>."
            exit 1
        fi

        echo "[deploy macbook] rsync local en ${REPO_LOCAL}…"
        rsync -a --delete \
            --exclude '.venv' --exclude '__pycache__' --exclude '.git' --exclude '.claude' \
            --exclude 'devices/*/.env' --exclude 'devices/*/config.yaml' \
            "$REPO_LOCAL_SELF/" "$REPO_LOCAL/"

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

        # El nombre acaba en una ruta (devices/<nombre>.env): issue #55.
        if ! jota_valid_id "$DEVICE_NAME"; then
            echo "ERROR: nombre de dispositivo inválido: '$DEVICE_NAME' (solo letras, dígitos, _ y -)."
            exit 1
        fi
        DEVICE_ENV="$DEVICES_DIR/${DEVICE_NAME}.env"
        if [ ! -f "$DEVICE_ENV" ]; then
            echo "ERROR: $DEVICE_ENV no existe."
            exit 1
        fi

        # Issue #56: los .env de devices/ llevan credenciales; 600 en cada deploy.
        jota_secure_perms "$REPO_LOCAL_SELF" >/dev/null

        set -a
        # shellcheck disable=SC1090
        . "$DEVICE_ENV"
        set +a

        for var in PHONE_HOST PHONE_PORT PHONE_USER PHONE_DIR; do
            eval val=\$$var
            if [ -z "$val" ]; then
                echo "ERROR: $var no definida en $DEVICE_ENV"
                exit 1
            fi
        done

        PHONE_TARGET="${PHONE_USER}@${PHONE_HOST}"

        # Con PHONE_PASS vacío se usa la clave SSH (ssh-copy-id -p 8022 ...).
        # Con contraseña, sshpass -e la lee de $SSHPASS: con -p quedaba
        # visible para cualquier usuario en `ps` mientras dura la conexión.
        _sshpass=""
        if [ -n "${PHONE_PASS:-}" ]; then
            command -v sshpass >/dev/null 2>&1 || {
                echo "ERROR: PHONE_PASS definido pero falta sshpass (brew install sshpass). Mejor: usa una clave SSH y deja PHONE_PASS vacío."
                exit 1
            }
            export SSHPASS="$PHONE_PASS"
            unset PHONE_PASS
            _sshpass="sshpass -e"
        fi

        _phone_ssh() {
            $_sshpass ssh -p "$PHONE_PORT" "$PHONE_TARGET" "$@"
        }
        _phone_rsync() {
            $_sshpass rsync -a --delete \
                -e "ssh -p $PHONE_PORT" \
                --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
                --exclude 'config.yaml' --exclude 'devices' \
                "$@"
        }

        echo "[deploy phone:${DEVICE_NAME}] sincronizando con ${PHONE_TARGET}:${PHONE_DIR}…"
        _phone_ssh "mkdir -p '$PHONE_DIR'"
        _phone_rsync "$REPO_LOCAL_SELF/" "${PHONE_TARGET}:${PHONE_DIR}/"

        echo "[deploy phone:${DEVICE_NAME}] reiniciando jota-voice vía supervisorctl…"
        # shellcheck disable=SC2016  # $HOME se expande en el teléfono
        _phone_ssh 'supervisorctl -c "$HOME/supervisord.conf" restart jota-voice'
        echo "[deploy phone:${DEVICE_NAME}] OK"
        ;;

    *)
        echo "Uso: $0 [phone|macbook]"
        exit 1
        ;;
esac
