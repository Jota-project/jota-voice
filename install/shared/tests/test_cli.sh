#!/bin/sh
# shellcheck disable=SC2015,SC2016  # ok/bad devuelven 0; los $ de los printf son de los scripts falsos
# Tests del CLI jota-voice (gestión de teléfonos desde el Mac) con ssh,
# rsync, sshpass y sleep falsos: registran sus argumentos, no conectan.
#
# Uso: sh install/shared/tests/test_cli.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/repo/devices" "$TMP/bin"
cp "$ROOT/jota-voice" "$TMP/repo/"
LOG="$TMP/argv.log"

for tool in ssh rsync; do
    printf '#!/bin/sh\necho "%s $*" >> "%s"\n' "$tool" "$LOG" > "$TMP/bin/$tool"
done
printf '#!/bin/sh\necho "sshpass $* [SSHPASS=${SSHPASS:+set}]" >> "%s"\nshift\nexec "$@"\n' "$LOG" > "$TMP/bin/sshpass"
printf '#!/bin/sh\nexit 0\n' > "$TMP/bin/sleep"
chmod +x "$TMP/bin/"*

fail=0
ok()  { echo "ok: $1"; }
bad() { echo "FAIL: $1"; fail=1; }
cli() { PATH="$TMP/bin:$PATH" bash "$TMP/repo/jota-voice" "$@"; }

cat > "$TMP/repo/devices/tel.env" <<'EOF'
DEVICE_NAME="tel"
PHONE_HOST="192.0.2.1"
PHONE_PORT="8022"
PHONE_USER="u0_aXXX"
PHONE_PASS=""
PHONE_DIR="/data/data/com.termux/files/home/jota-voice"
EOF

# --- update: recarga supervisord tras reescribir ~/supervisord.conf -------
: > "$LOG"
cli tel update > "$TMP/out" 2>&1 || bad "update falló: $(cat "$TMP/out")"
reread=$(grep -n "supervisorctl -c ~/supervisord.conf reread" "$LOG" | head -1 | cut -d: -f1)
update=$(grep -n "supervisorctl -c ~/supervisord.conf update" "$LOG" | head -1 | cut -d: -f1)
start=$(grep -n "supervisorctl -c ~/supervisord.conf start" "$LOG" | head -1 | cut -d: -f1)
if [ -n "$reread" ] && [ -n "$update" ] && [ -n "$start" ] && [ "$update" -lt "$start" ]; then
    ok "update: reread+update de supervisord antes de arrancar"
else
    bad "update sin reread/update: $(cat "$LOG")"
fi

# --- init: el .env se escribe con comillas simples escapadas --------------
# Contraseña con $( ), comillas dobles y simples y backticks: al cargar el
# .env con `.` no debe ejecutar nada y debe conservarse literal.
: > "$TMP/repo/config.yaml"
PASS='a$(touch "$TMPDIR_PWN/pwned")"b'"'"'c`id`$HOME'
export TMPDIR_PWN="$TMP"
printf '%s\n' "N" "" "nuevo" "192.0.2.9" "" "u0_aXXX" "$PASS" | cli init > "$TMP/out" 2>&1 \
    || bad "init falló: $(cat "$TMP/out")"
if [ -e "$TMP/pwned" ]; then bad "init: la contraseña ejecutó código"; else ok "init: nada ejecutado al cargar el .env"; fi
got=$(bash -c '. "$1"; printf "%s" "$PHONE_PASS"' _ "$TMP/repo/devices/nuevo.env")
[ "$got" = "$PASS" ] && ok "init: contraseña literal" || bad "init: contraseña '$got' (esperada '$PASS')"
got=$(bash -c '. "$1"; printf "%s|%s|%s" "$PHONE_HOST" "$PHONE_PORT" "$PHONE_USER"' _ "$TMP/repo/devices/nuevo.env")
[ "$got" = "192.0.2.9|8022|u0_aXXX" ] && ok "init: host/puerto/usuario" || bad "init: $got"
[ ! -e "$TMP/pwned" ] || bad "init: código ejecutado al releer"
rm -f "$TMP/repo/devices/nuevo.env" "$TMP/repo/config.yaml"

exit $fail
