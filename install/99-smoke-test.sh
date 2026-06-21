#!/bin/sh
set -e
source "$REPO_DIR"/lib/output.sh

_apply() {
    if [ -f "$HOME/supervisord.pid" ] && kill -0 "$(cat "$HOME/supervisord.pid")" 2>/dev/null; then
        _info "Deteniendo supervisord anterior"
        supervisorctl -c "$HOME/supervisord.conf" shutdown 2>/dev/null || true
        sleep 2
    fi

    _info "Arrancando supervisord"
    supervisord -c "$HOME/supervisord.conf"
    sleep 10

    echo ""
    echo "=== Estado de servicios ==="
    supervisorctl -c "$HOME/supervisord.conf" status
    echo ""

    _all_ok=true
    for svc in pulseaudio oww jota-display jota-voice; do
        status=$(supervisorctl -c "$HOME/supervisord.conf" status "$svc" 2>/dev/null | awk '{print $2}')
        if [ "$status" = "RUNNING" ] || [ "$status" = "STARTING" ]; then
            _ok "$svc: $status"
        else
            echo "  ✗ $svc: $status" >&2
            _all_ok=false
        fi
    done

    echo ""
    # Verificar que OWW acepta conexiones TCP en el puerto 10401
    _info "Verificando OWW (puerto 10401)..."
    OWW_OK=false
    for _i in 1 2 3 4 5; do
        if python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(3)
r = s.connect_ex(('127.0.0.1', 10401))
s.close()
sys.exit(0 if r == 0 else 1)
" 2>/dev/null; then
            OWW_OK=true
            break
        fi
        sleep 3
    done
    if $OWW_OK; then
        _ok "OWW: escuchando en :10401"
    else
        echo "  ✗ OWW: no responde en :10401 tras 15s" >&2
        _all_ok=false
    fi

    echo ""
    if $_all_ok; then
        echo "✓ install.sh completado — todos los servicios arrancados"
    else
        echo "⚠ install.sh completado con advertencias"
        exit 1
    fi
}

_apply