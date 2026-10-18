#!/bin/sh
# platform.sh — Detección de plataforma para la capa de instalación (shell).
#
# Equivalente shell de client/core/platform_key.py::detect_platform(), con el
# MISMO orden para que Python y shell nunca discrepen:
#   1. Darwin                                  → darwin
#   2. $PREFIX contiene "com.termux"           → termux
#   3. Existe el fichero hosts de Termux       → termux (PREFIX saneado por init)
#   4. Linux                                   → linux
#   5. MINGW/MSYS/CYGWIN/Windows_NT            → windows
#   6. Otro                                    → unknown (return 1)
#
# Entradas sobrescribibles (para tests):
#   JOTA_UNAME_S             — en vez de `uname -s`
#   JOTA_TERMUX_HOSTS_PATH   — en vez de la ruta real del hosts de Termux
#
# Uso: . lib/platform.sh; plat=$(jota_detect_platform)

JOTA_TERMUX_HOSTS_DEFAULT="/data/data/com.termux/files/usr/etc/hosts"

jota_detect_platform() {
    _jp_uname="${JOTA_UNAME_S:-$(uname -s 2>/dev/null || echo unknown)}"
    _jp_hosts="${JOTA_TERMUX_HOSTS_PATH:-$JOTA_TERMUX_HOSTS_DEFAULT}"

    case "$_jp_uname" in
        Darwin) echo darwin; return 0 ;;
    esac
    case "${PREFIX:-}" in
        *com.termux*) echo termux; return 0 ;;
    esac
    if [ -e "$_jp_hosts" ]; then
        echo termux
        return 0
    fi
    case "$_jp_uname" in
        Linux) echo linux ;;
        MINGW*|MSYS*|CYGWIN*|Windows_NT) echo windows ;;
        *) echo unknown; return 1 ;;
    esac
}
