#!/bin/sh
# Crea devices/${DEVICE_ID}/config.yaml si no existe.
#
# Política de secretos (importante):
#   - Este script NO contiene URLs, claves ni secretos hardcodeados.
#   - Los valores del gateway se leen, en este orden:
#       1. Variables de entorno GATEWAY_URL y GATEWAY_CLIENT_KEY
#          (típicamente exportadas desde .env.local en el repo raíz,
#          o desde un .env del usuario — ambos en .gitignore).
#       2. Si faltan, se leen interactivamente UNA sola vez y se persisten
#          en devices/<id>/config.yaml (gitignored, chmod 600).
#   - El resultado es que NUNCA aparece una URL del despliegue real
#     hardcodeada en este archivo versionado.
#
# Idempotente: si devices/<id>/config.yaml ya existe, no pregunta nada
# ni lo toca.
set -e
. "$(dirname "$0")/00-lib.sh"

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

# --- Resolución de secretos SIN hardcodear nada en este script -----------
#
# Si el usuario ya tiene GATEWAY_URL y GATEWAY_CLIENT_KEY en su entorno
# (exportadas desde .env.local o similar), las usamos directamente.
# Si no, las preguntamos UNA sola vez y se guardan en $CFG (gitignored).

if [ -z "${GATEWAY_URL:-}" ]; then
    _info "Configurando dispositivo '$ID' — falta GATEWAY_URL en el entorno"
    printf "  URL del gateway (ws:// o wss://): "
    read -r GATEWAY_URL
    while [ -z "$GATEWAY_URL" ]; do
        printf "  la URL no puede estar vacía. Introdúcela: "
        read -r GATEWAY_URL
    done
fi

if [ -z "${GATEWAY_CLIENT_KEY:-}" ]; then
    printf "  client_key (obligatorio): "
    read -r GATEWAY_CLIENT_KEY
    while [ -z "$GATEWAY_CLIENT_KEY" ]; do
        printf "  client_key no puede estar vacío. Introdúcelo: "
        read -r GATEWAY_CLIENT_KEY
    done
fi

# wake word: el único default legítimo del script — ok_nabu es el modelo
# bundled upstream de openWakeWord, no un secreto del usuario.
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
# Generado por install/macos/02-config-wizard.sh. Gitignored.
gateway:
  url: "${GATEWAY_URL}"
  client_key: "${GATEWAY_CLIENT_KEY}"
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
_ok "Creado $CFG (chmod 600)"
_ok "Tip: en próximos installs exporta GATEWAY_URL y GATEWAY_CLIENT_KEY desde .env.local y el wizard no preguntará nada"