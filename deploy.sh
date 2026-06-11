#!/usr/bin/env bash
# Despliega jota-voice al teléfono (Termux) via SSH.
# Requiere: sshpass, .env.local con PHONE_HOST, PHONE_PORT, PHONE_PASS, PHONE_DIR

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_LOCAL="$REPO_DIR/.env.local"

if [ ! -f "$ENV_LOCAL" ]; then
    echo "ERROR: no existe .env.local"
    echo "       cp .env.local.example .env.local && editar"
    exit 1
fi
set -a; . "$ENV_LOCAL"; set +a

for var in PHONE_HOST PHONE_PORT PHONE_PASS PHONE_DIR; do
    eval val=\$$var
    [ -z "$val" ] && echo "ERROR: $var no definida" && exit 1
done

_ssh() { sshpass -p "$PHONE_PASS" ssh -p "$PHONE_PORT" "$PHONE_HOST" "$@"; }
_rsync() {
    sshpass -p "$PHONE_PASS" rsync -av \
        -e "ssh -p $PHONE_PORT" \
        --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
        --exclude='config.yaml' \
        "$@"
}

echo "→ Sincronizando $PHONE_HOST:$PHONE_DIR"
_ssh "mkdir -p $PHONE_DIR"
_rsync "$REPO_DIR/" "$PHONE_HOST:$PHONE_DIR/"

echo "→ Reiniciando jota-voice-client"
_ssh "pkill -f 'voice_client.py' 2>/dev/null || true; sleep 1
      cd $PHONE_DIR && nohup sh boot/start.sh </dev/null >>/dev/null 2>&1 &"

echo "✓ Desplegado"
