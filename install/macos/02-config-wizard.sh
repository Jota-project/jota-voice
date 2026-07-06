#!/bin/sh
# Crea devices/${DEVICE_ID}/config.yaml interactivamente si no existe.
# Idempotente: si ya existe, no pregunta nada ni lo toca.
set -e
. "$(dirname "$0")/00-lib.sh"

# Si el dispositivo por defecto ya tiene config, no preguntamos nada —
# ni siquiera el device id (evita romper una ejecución no interactiva).
DEFAULT_CFG="$REPO_DIR/devices/$DEVICE_ID/config.yaml"
if [ -f "$DEFAULT_CFG" ]; then
    _ok "$DEFAULT_CFG ya existe, no se toca (bórralo a mano si quieres regenerarlo)"
    exit 0
fi

printf "  device id [%s]: " "$DEVICE_ID"
read -r INPUT_ID
ID="${INPUT_ID:-$DEVICE_ID}"
CFG="$REPO_DIR/devices/$ID/config.yaml"

if [ -f "$CFG" ]; then
    _ok "$CFG ya existe, no se toca (bórralo a mano si quieres regenerarlo)"
    exit 0
fi

_info "Configurando dispositivo '$ID' — pulsa Enter para aceptar el valor por defecto"

printf "  client_key (obligatorio): "
read -r CLIENT_KEY
while [ -z "$CLIENT_KEY" ]; do
    printf "  client_key no puede estar vacío. Introdúcelo: "
    read -r CLIENT_KEY
done

printf "  URL del gateway (ws:// o wss://) [wss://green-house.alfonsogare.com/api/gateway/ws/stream]: "
read -r GW_URL
GW_URL="${GW_URL:-wss://green-house.alfonsogare.com/api/gateway/ws/stream}"

printf "  wake word [ok_nabu]: "
read -r WAKE_WORD
WAKE_WORD="${WAKE_WORD:-ok_nabu}"

if [ "$WAKE_WORD" != "ok_nabu" ]; then
    printf "  ruta al modelo .tflite de '%s' (vacío = ya está instalado en %s): " "$WAKE_WORD" "$OWW_DATA_DIR"
    read -r MODEL_PATH
    if [ -n "$MODEL_PATH" ]; then
        if [ ! -f "$MODEL_PATH" ]; then
            _err "No existe el fichero: $MODEL_PATH"
            exit 1
        fi
        mkdir -p "$OWW_DATA_DIR"
        cp -f "$MODEL_PATH" "$OWW_DATA_DIR/${WAKE_WORD}.tflite"
        _ok "Modelo copiado a $OWW_DATA_DIR/${WAKE_WORD}.tflite"
    fi
fi

mkdir -p "$(dirname "$CFG")"
cat > "$CFG" <<EOF
gateway:
  url: "${GW_URL}"
  client_key: "${CLIENT_KEY}"
  connect_timeout_s: 10

device:
  id: "${ID}"

oww:
  backend: "wyoming"
  host: "127.0.0.1"
  port: 10401
  wake_words:
    - "${WAKE_WORD}"
  reconnect_backoff_s: [5, 10, 20, 60]

audio:
  backend: "sounddevice"
  sample_rate: 16000
  channels: 1
  frames_per_buffer: 512
  preroll_seconds: 1.5
  silence_timeout_s: 1.5
  recording_timeout_s: 15.0
  vad_rms_threshold: 200
  input_device: null
  output_device: null

display:
  backend: "null"

logging:
  level: "INFO"

control:
  port: 8765
EOF
chmod 600 "$CFG"
_ok "Creado $CFG"
