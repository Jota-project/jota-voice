#!/bin/sh
# device.sh — Id de dispositivo por defecto, sin valores del despliegue real
# en el código versionado (issue #101).
#
# jota_default_device_id <repo_dir>
#   1. $DEVICE_ID si está fijado.
#   2. Si hay exactamente un devices/<id>/config.yaml, ese <id> (así una
#      máquina ya instalada sigue funcionando sin exportar nada).
#   3. Si no hay ninguno, el hostname corto saneado (minúsculas, [a-z0-9_-]).
#   Con VARIOS devices/*/config.yaml y sin DEVICE_ID falla (return 1, mensaje
#   en stderr): adivinar por hostname podía elegir uno que no existe.

jota_default_device_id() {
    if [ -n "${DEVICE_ID:-}" ]; then
        echo "$DEVICE_ID"
        return 0
    fi
    _jd_count=0
    _jd_match=""
    for _jd_f in "$1"/devices/*/config.yaml; do
        [ -f "$_jd_f" ] || continue
        _jd_count=$((_jd_count + 1))
        _jd_match="$_jd_f"
    done
    if [ "$_jd_count" -eq 1 ]; then
        basename "$(dirname "$_jd_match")"
        return 0
    fi
    if [ "$_jd_count" -gt 1 ]; then
        echo "  ✗ Hay varios devices/*/config.yaml en $1: define DEVICE_ID=<id> y reintenta." >&2
        return 1
    fi
    _jd_host="$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo dispositivo)"
    _jd_host="$(printf '%s' "$_jd_host" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '_' | cut -c1-64)"
    case "$_jd_host" in
        ""|-*) _jd_host="dispositivo" ;;
    esac
    echo "$_jd_host"
}
