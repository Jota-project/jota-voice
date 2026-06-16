#!/bin/sh
set -e
source ../lib/output.sh

_apply() {
    if [ -S "$HOME/supervisor.sock" ]; then
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
    for svc in oww jota-display jota-voice; do
        status=$(supervisorctl -c "$HOME/supervisord.conf" status "$svc" 2>/dev/null | awk '{print $2}')
        if [ "$status" = "RUNNING" ] || [ "$status" = "STARTING" ]; then
            _ok "$svc: $status"
        else
            echo "  ✗ $svc: $status" >&2
            _all_ok=false
        fi
    done

    echo ""
    if $_all_ok; then
        echo "✓ install.sh completado — todos los servicios arrancados"
    else
        echo "⚠ install.sh completado con advertencias"
        exit 1
    fi
}

_apply