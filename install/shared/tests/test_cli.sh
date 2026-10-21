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

exit $fail
