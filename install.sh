#!/data/data/com.termux/files/usr/bin/sh
# Primer setup de jota-voice-client en un dispositivo Termux nuevo.
# Ejecutar una sola vez tras clonar el repo.

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[install] Instalando dependencias del sistema..."
# python-numpy via pkg (binario ARM, evitar compilar desde source)
# portaudio necesario para compilar pyaudio via pip
pkg install -y python python-numpy portaudio

echo "[install] Creando venv con system-site-packages..."
python -m venv --system-site-packages "$REPO_DIR/.venv"
. "$REPO_DIR/.venv/bin/activate"

echo "[install] Instalando dependencias pip..."
pip install -r "$REPO_DIR/client/requirements.txt"

echo "[install] Copiando config de ejemplo..."
if [ ! -f "$REPO_DIR/config.yaml" ]; then
    cp "$REPO_DIR/config.example.yaml" "$REPO_DIR/config.yaml"
    echo "[install] EDITAR config.yaml con tu client_key y IPs"
fi

echo "[install] Configurando Termux:Boot..."
BOOT_DIR="$HOME/.termux/boot"
mkdir -p "$BOOT_DIR"
cat > "$BOOT_DIR/jota-voice" << EOF
#!/data/data/com.termux/files/usr/bin/sh
exec sh $REPO_DIR/boot/start.sh
EOF
chmod +x "$BOOT_DIR/jota-voice"

echo ""
echo "[install] ✓ Instalación completa"
echo "  Siguiente paso: editar config.yaml y rellenar client_key"
