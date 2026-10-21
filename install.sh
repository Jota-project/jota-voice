#!/bin/sh
# install.sh — Punto de entrada ÚNICO de instalación de jota-voice.
#
# Detecta la plataforma (lib/platform.sh, mismo orden que
# client/core/platform_key.py) y despacha al instalador que toca:
#   termux → install/install-termux.sh   (Android, supervisord + Termux:Boot)
#   darwin → install/macos/install.sh    (macOS, LaunchAgents)
#   linux  → todavía sin instalador (issue #54)
#
# Uso:
#   sh install.sh             # instala en la plataforma detectada
#   sh install.sh --detect    # solo imprime la plataforma detectada
#
# JOTA_PLATFORM=<termux|darwin|linux> fuerza la plataforma (diagnóstico).
set -u

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
export REPO_DIR

. "$REPO_DIR/lib/platform.sh"

PLATFORM="${JOTA_PLATFORM:-$(jota_detect_platform)}"

if [ "${1:-}" = "--detect" ]; then
    echo "$PLATFORM"
    exit 0
fi

case "$PLATFORM" in
    termux)
        echo "jota-voice: plataforma Termux (Android)"
        # bash explícito: los pasos usan `source` y el sh de Termux es dash.
        exec bash "$REPO_DIR/install/install-termux.sh" "$@"
        ;;
    darwin)
        echo "jota-voice: plataforma macOS"
        exec sh "$REPO_DIR/install/macos/install.sh" "$@"
        ;;
    linux)
        echo "jota-voice: Linux de escritorio detectado." >&2
        echo "  Todavía no hay instalador para Linux (issue #54)." >&2
        echo "  El cliente funciona (audio sounddevice, display ws), pero hay que" >&2
        echo "  montarlo a mano: venv con client/requirements.txt, servidor Wyoming" >&2
        echo "  OpenWakeWord en 127.0.0.1:10401 y config en ~/.config/jota-voice/config.yaml" >&2
        echo "  (plantilla: config.example.yaml, display.backend: \"ws\")." >&2
        exit 2
        ;;
    windows)
        echo "jota-voice: Windows no está soportado todavía." >&2
        exit 2
        ;;
    *)
        echo "jota-voice: plataforma no reconocida ($(uname -s 2>/dev/null))." >&2
        echo "  Soportadas: Termux (Android), macOS. Linux: pendiente (issue #54)." >&2
        exit 2
        ;;
esac
