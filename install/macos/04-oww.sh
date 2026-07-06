#!/bin/sh
# Levanta wyoming-openwakeword en Docker y espera a que el puerto 10401 responda.
set -e
. "$(dirname "$0")/00-lib.sh"

CONTAINER_NAME="wyoming-oww"
IMAGE="rhasspy/wyoming-openwakeword"
PORT=10401

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
        --threshold 0.3
fi
_ok "Contenedor $CONTAINER_NAME arrancado"

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
