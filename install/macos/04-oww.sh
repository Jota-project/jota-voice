#!/bin/sh
# Levanta wyoming-openwakeword en Docker y espera a que el puerto 10401 responda.
set -e
. "$(dirname "$0")/00-lib.sh"

CONTAINER_NAME="wyoming-oww"
# Pin a 1.10.0: la 2.1.0 (latest) está rota en upstream
# (rhasspy/wyoming-openwakeword#53 — modelos .tflite incompatibles con pyopen_wakeword 1.1.0).
# No subir a 2.x sin verificar contra el issue primero.
IMAGE="rhasspy/wyoming-openwakeword:1.10.0"
PORT=10401

# Threshold de detección. OpenWakeWord recomienda 0.1-0.2 con audio de micro real.
# Configurable por env: OWW_THRESHOLD=0.10 sh install/macos/04-oww.sh
OWW_THRESHOLD="${OWW_THRESHOLD:-0.15}"

_info "Asegurando imagen $IMAGE…"
docker pull "$IMAGE" >/dev/null
_ok "Imagen actualizada"

_info "Creando directorio de datos $OWW_DATA_DIR…"
mkdir -p "$OWW_DATA_DIR"

# Si ya existe el contenedor, asegúrate de que está corriendo.
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    _info "Contenedor $CONTAINER_NAME ya existe; arrancando…"
    docker start "$CONTAINER_NAME" >/dev/null
else
    _info "Creando contenedor $CONTAINER_NAME…"
    # --model/--preload-model están deprecados en wyoming-openwakeword;
    # --custom-model-dir carga cualquier .tflite que encuentre en /data
    # (verificado: reporta "Found custom model <nombre> at /data/<nombre>.tflite").
    docker run -d \
        --name "$CONTAINER_NAME" \
        --restart unless-stopped \
        -p "${PORT}:${PORT}" \
        -v "${OWW_DATA_DIR}:/data" \
        "$IMAGE" \
        --uri "tcp://0.0.0.0:${PORT}" \
        --custom-model-dir /data \
        --threshold "$OWW_THRESHOLD"
fi
_ok "Contenedor $CONTAINER_NAME arrancado"
_info "Imagen: $IMAGE  |  threshold: $OWW_THRESHOLD"

_info "Esperando a que el puerto ${PORT} responda…"
deadline=$((SECONDS + 30))
until bash -c "exec 3<>/dev/tcp/127.0.0.1/${PORT} && exec 3>&-" 2>/dev/null; do
    if [ $SECONDS -ge $deadline ]; then
        _err "Timeout esperando puerto ${PORT}. Comprueba: docker logs $CONTAINER_NAME"
        exit 1
    fi
    sleep 1
done
_ok "Puerto ${PORT} responde (Wyoming listo)"
