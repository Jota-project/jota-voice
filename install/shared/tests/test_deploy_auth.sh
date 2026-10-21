#!/bin/sh
# shellcheck disable=SC2015,SC2016  # ok/bad devuelven 0; los $ del printf son de los scripts falsos
# Test de deploy.sh phone (issue #56): la contraseña del teléfono nunca va
# en la línea de órdenes (sshpass -e, no -p), con PHONE_PASS vacío se usa
# la clave SSH sin sshpass, y devices/<nombre>.env queda en 600.
#
# ssh, rsync y sshpass son falsos: registran sus argumentos, no conectan.
#
# Uso: sh install/shared/tests/test_deploy_auth.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
. "$ROOT/lib/secure.sh"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/repo/lib" "$TMP/repo/devices" "$TMP/bin"
cp "$ROOT/deploy.sh" "$TMP/repo/"
cp "$ROOT/lib/secure.sh" "$TMP/repo/lib/"
LOG="$TMP/argv.log"

for tool in ssh rsync; do
    printf '#!/bin/sh\necho "%s $*" >> "%s"\n' "$tool" "$LOG" > "$TMP/bin/$tool"
done
# sshpass falso: registra argumentos y si recibió SSHPASS por entorno.
printf '#!/bin/sh\necho "sshpass $* [SSHPASS=${SSHPASS:+set}]" >> "%s"\nshift\nexec "$@"\n' "$LOG" > "$TMP/bin/sshpass"
chmod +x "$TMP/bin/"*

write_env() {
    cat > "$TMP/repo/devices/tel.env" <<EOF
PHONE_HOST="192.0.2.1"
PHONE_PORT="8022"
PHONE_USER="u"
PHONE_PASS="$1"
PHONE_DIR="/data/data/com.termux/files/home/jota-voice"
EOF
    chmod 644 "$TMP/repo/devices/tel.env"
}

fail=0
ok()  { echo "ok: $1"; }
bad() { echo "FAIL: $1"; fail=1; }

# 1. Con contraseña.
write_env "secreto-de-prueba"
: > "$LOG"
PATH="$TMP/bin:$PATH" bash "$TMP/repo/deploy.sh" phone tel > "$TMP/out" 2>&1 || bad "deploy con contraseña falló: $(cat "$TMP/out")"
if grep -q "secreto-de-prueba" "$LOG"; then bad "la contraseña aparece en argv"; else ok "contraseña fuera de argv"; fi
if grep -q "^sshpass -e .*\[SSHPASS=set\]" "$LOG"; then ok "sshpass -e con SSHPASS"; else bad "sin sshpass -e: $(cat "$LOG")"; fi
[ "$(_jota_file_mode "$TMP/repo/devices/tel.env")" = "600" ] && ok "tel.env → 600" || bad "tel.env no está en 600"

# 2. Sin contraseña: clave SSH, sin sshpass.
write_env ""
: > "$LOG"
PATH="$TMP/bin:$PATH" bash "$TMP/repo/deploy.sh" phone tel > "$TMP/out" 2>&1 || bad "deploy con clave falló: $(cat "$TMP/out")"
if grep -q "^sshpass" "$LOG"; then bad "usó sshpass sin contraseña"; else ok "clave SSH: sin sshpass"; fi
grep -q "^ssh -p 8022 u@192.0.2.1" "$LOG" && ok "ssh directo" || bad "no llamó a ssh: $(cat "$LOG")"

# --- macbook: origen = directorio del script; destino obligatorio ---------
cp "$ROOT/lib/device.sh" "$TMP/repo/lib/"
mkdir -p "$TMP/repo/devices/mac" "$TMP/home" "$TMP/otro-cwd"
echo "x: y" > "$TMP/repo/devices/mac/config.yaml"
printf '#!/bin/sh\necho "launchctl $*" >> "%s"\n' "$LOG" > "$TMP/bin/launchctl"
chmod +x "$TMP/bin/launchctl"

: > "$LOG"
if (cd "$TMP/otro-cwd" && env -u REPO_LOCAL HOME="$TMP/home" PATH="$TMP/bin:$PATH" bash "$TMP/repo/deploy.sh" macbook) > "$TMP/out" 2>&1; then
    bad "macbook sin REPO_LOCAL no falló"
else
    grep -q "^rsync" "$LOG" && bad "macbook sin REPO_LOCAL hizo rsync" || ok "macbook: REPO_LOCAL obligatorio"
fi

: > "$LOG"
if (cd "$TMP/otro-cwd" && REPO_LOCAL="$TMP/repo" HOME="$TMP/home" PATH="$TMP/bin:$PATH" bash "$TMP/repo/deploy.sh" macbook) > "$TMP/out" 2>&1; then
    bad "macbook hacia sí mismo no falló"
else
    ok "macbook: rechaza REPO_LOCAL = este checkout"
fi

# Copia de despliegue ya instalada (con su config) y lanzado desde otro cwd.
mkdir -p "$TMP/dest/devices/mac" && echo "x: y" > "$TMP/dest/devices/mac/config.yaml"
: > "$LOG"
(cd "$TMP/otro-cwd" && REPO_LOCAL="$TMP/dest" DEVICE_ID=mac HOME="$TMP/home" PATH="$TMP/bin:$PATH" bash "$TMP/repo/deploy.sh" macbook) > "$TMP/out" 2>&1 \
    || bad "macbook válido falló: $(cat "$TMP/out")"
grep "^rsync" "$LOG" | grep -q " $TMP/repo/ $TMP/dest/$" && ok "macbook: rsync desde el directorio del script" \
    || bad "macbook: origen incorrecto: $(grep '^rsync' "$LOG")"

exit $fail
