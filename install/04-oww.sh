#!/bin/sh
set -e
source "$REPO_DIR"/lib/output.sh

OWW_VENV="$HOME/oww-venv"

_model_exists() {
    find "$OWW_VENV/lib" -name "ok_nabu*" 2>/dev/null | grep -q "ok_nabu"
}

_check() {
    [ -d "$OWW_VENV" ] && _model_exists
}

_apply() {
    _info "Configurando oww-venv (system-site-packages para tflite/scipy/onnx)"
    python -m venv --system-site-packages "$OWW_VENV"
    _info "Instalando openwakeword y wyoming-openwakeword"
    # tflite-runtime y onnxruntime vienen via Termux pkg (system-site-packages).
    # Instalamos el motor (openwakeword) y el adaptador (wyoming-openwakeword) sin sus
    # deps de inferencia para evitar que pip intente bajar wheels inexistentes en Python 3.13.
    "$OWW_VENV/bin/pip" install -q --no-deps openwakeword==0.5.1 wyoming-openwakeword==1.3.0
    # Resto de deps de openwakeword que sí tienen wheels para Python 3.13
    # audioop-lts: backport de audioop (eliminado en Python 3.13) que usa wyoming==1.1.0
    # scipy llega via python-scipy de Termux (system-site-packages); pip lo compilaría desde fuente
    "$OWW_VENV/bin/pip" install -q audioop-lts "wyoming==1.1.0" requests joblib tqdm

    # Verificar que ok_nabu está disponible (v1.3.0 lo bundlea; descarga como fallback)
    MODELS_DIR="$OWW_VENV/lib/python3.13/site-packages/wyoming_openwakeword/models"
    if ! find "$MODELS_DIR" -name "ok_nabu*" 2>/dev/null | grep -q "ok_nabu"; then
        _info "Descargando modelo ok_nabu (no bundleado)"
        curl -fsSL -o "$MODELS_DIR/ok_nabu_v0.1.tflite" \
            "https://raw.githubusercontent.com/dscripka/openWakeWord/main/openwakeword/resources/models/ok_nabu_v0.1.tflite" \
        || curl -fsSL -o "$MODELS_DIR/ok_nabu_v0.1.tflite" \
            "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/ok_nabu_v0.1.tflite"
    fi
    _ok "OWW instalado con modelo ok_nabu"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "OWW ya instalado" || exit 1
else
    _apply
fi
