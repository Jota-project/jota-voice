#!/bin/sh
# Levanta wyoming-openwakeword como venv Python nativo (sin Docker)
# y lo supervisa vía launchd. Escucha en el puerto 10401.
#
# Diferencias respecto al diseño anterior (Docker):
#   - Docker Desktop en macOS ejecuta los contenedores en una VM LinuxKit
#     que paga un overhead de 1-2GB de RAM. El servidor Wyoming OWW en sí
#     es pequeño (modelo TFLite + runtime de inferencia).
#   - wyoming-openwakeword publica tflite-runtime-nightly como dependencia,
#     pero PyPI no ofrece wheels para macOS. Solución: instalar el paquete
#     con --no-deps y cubrir la importación con el shim local
#     `tflite_runtime_shim`, que re-exporta ai_edge_litert.interpreter.
#   - El bug del issue rhasspy/wyoming-openwakeword#53 (imagen Docker 2.1.0
#     migrada a pyopen_wakeword) NO afecta al paquete pip, que sigue
#     importando tflite_runtime.interpreter. El pin a wyoming-openwakeword
#     == 1.8.2 protege contra un posible regreso a pyopen_wakeword en el
#     futuro.
#
# Subcomandos:
#   install   (por defecto) — crea venv, instala paquetes + shim,
#                              genera LaunchAgent, lo arranca.
#   uninstall              — para el servicio, borra .plist y venv.
#                              Deja $OWW_DATA_DIR intacto.
set -e
. "$(dirname "$0")/00-lib.sh"

CMD="${1:-install}"
PORT=10401
OWW_THRESHOLD="${OWW_THRESHOLD:-0.40}"

_info "Modo: $CMD"

# Ruta del shim local — se instala con `pip install -e .` desde aquí.
shim_dir="$REPO_DIR/install/macos/tflite_runtime_shim"

# ---------------------------------------------------------------- uninstall
if [ "$CMD" = "uninstall" ]; then
    USER_UID="$(id -u)"
    PLIST_LABEL="com.jota-voice.wyoming-oww"
    PLIST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

    if launchctl print "gui/${USER_UID}/${PLIST_LABEL}" >/dev/null 2>&1; then
        _info "Parando servicio $PLIST_LABEL…"
        launchctl bootout "gui/${USER_UID}/${PLIST_LABEL}" 2>/dev/null || true
        _ok "Servicio parado"
    else
        _info "Servicio $PLIST_LABEL no estaba cargado"
    fi

    if [ -f "$PLIST" ]; then
        rm -f "$PLIST"
        _ok "plist borrado: $PLIST"
    fi

    if [ -d "$OWW_VENV_DIR" ]; then
        rm -rf "$OWW_VENV_DIR"
        _ok "venv borrado: $OWW_VENV_DIR"
    fi

    _ok "Desinstalación completa (datos en $OWW_DATA_DIR intactos)"
    exit 0
fi

# ------------------------------------------------------------------- install
_info "Asegurando venv en $OWW_VENV_DIR…"
mkdir -p "$(dirname "$OWW_VENV_DIR")"
if [ ! -d "$OWW_VENV_DIR" ]; then
    python3 -m venv "$OWW_VENV_DIR"
    _ok "venv creado"
else
    _ok "venv ya existe"
fi

_info "Actualizando pip…"
"$OWW_VENV_DIR/bin/pip" install --quiet --upgrade pip wheel setuptools
_ok "pip actualizado"

_info "Instalando shim tflite_runtime (paquete local)…"
"$OWW_VENV_DIR/bin/pip" install --quiet -e "$shim_dir"
_ok "shim instalado"

_info "Instalando wyoming-openwakeword==1.8.2 (--no-deps), wyoming==1.2.0 y ai-edge-litert…"
"$OWW_VENV_DIR/bin/pip" install --quiet wyoming==1.2.0 "wyoming-openwakeword==1.8.2" --no-deps
"$OWW_VENV_DIR/bin/pip" install --quiet ai-edge-litert
_ok "Paquetes instalados"

# Verificar que el shim se ve desde el Python del venv: si wyoming-openwakeword
# cambia su punto de import en una versión futura, este check falla antes
# de generar el LaunchAgent, evitando un servicio que arranca pero no detecta.
_info "Verificando que tflite_runtime.interpreter resuelve correctamente…"
if ! "$OWW_VENV_DIR/bin/python" -c "import tflite_runtime.interpreter as t; print(t.Interpreter)" >/dev/null 2>&1; then
    _err "El shim tflite_runtime no resuelve Interpreter. ai-edge-litert ha podido cambiar de API; revisa install/macos/tflite_runtime_shim/tflite_runtime/interpreter.py"
    exit 1
fi
_ok "Shim funcional"

# Comprobar que el módulo wyoming_openwakeword importa sin tocar la red.
_info "Verificando que wyoming_openwakeword importa correctamente…"
if err=$("$OWW_VENV_DIR/bin/python" -c "from wyoming_openwakeword.handler import OpenWakeWordEventHandler" 2>&1); then
    _ok "wyoming_openwakeword importa correctamente"
else
    _err "Import de wyoming_openwakeword falló:\n$err"
    exit 1
fi

# -------------------------------------------------------- generar LaunchAgent
USER_UID="$(id -u)"
PLIST_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs"
PLIST="$PLIST_DIR/com.jota-voice.wyoming-oww.plist"
PLIST_TEMPLATE="$REPO_DIR/install/macos/launchagent/com.jota-voice.wyoming-oww.plist"

mkdir -p "$PLIST_DIR" "$LOG_DIR"

_info "Generando LaunchAgent…"
sed \
    -e "s|__VENV_DIR__|$OWW_VENV_DIR|g" \
    -e "s|__DATA_DIR__|$OWW_DATA_DIR|g" \
    -e "s|__THRESHOLD__|${OWW_THRESHOLD}|g" \
    -e "s|__HOME__|$HOME|g" \
    "$PLIST_TEMPLATE" > "$PLIST"
chmod 644 "$PLIST"
_ok "plist generado: $PLIST"

# -------------------------------------------------------- arrancar LaunchAgent
# Si ya estaba cargado de una instalación previa, lo descargamos antes de
# recargar (paralelo a 07-launchd.sh).
launchctl bootout "gui/${USER_UID}/com.jota-voice.wyoming-oww" 2>/dev/null || true
launchctl bootstrap "gui/${USER_UID}" "$PLIST"
launchctl enable "gui/${USER_UID}/com.jota-voice.wyoming-oww"
launchctl kickstart -k "gui/${USER_UID}/com.jota-voice.wyoming-oww"
_ok "Servicio com.jota-voice.wyoming-oww arrancado"
_info "Logs: tail -f $LOG_DIR/com.jota-voice.wyoming-oww.log"

# ------------------------------------------------- esperar a que el puerto
_info "Esperando a que el puerto ${PORT} responda…"
deadline=$((SECONDS + 30))
until bash -c "exec 3<>/dev/tcp/127.0.0.1/${PORT} && exec 3>&-" 2>/dev/null; do
    if [ $SECONDS -ge $deadline ]; then
        _err "Timeout esperando puerto ${PORT}. Comprueba: tail -50 $LOG_DIR/com.jota-voice.wyoming-oww.log"
        exit 1
    fi
    sleep 1
done
_ok "Puerto ${PORT} responde (Wyoming listo)"

# ------------------------------------------------------- resumen final
_info "Imagen: pip (no Docker)  |  threshold: ${OWW_THRESHOLD}  |  puerto: ${PORT}"
_info "Si tenías un contenedor 'wyoming-oww' previo de Docker, puedes pararlo con: docker stop wyoming-oww"
_ok "Wyoming OWW nativo activo"
