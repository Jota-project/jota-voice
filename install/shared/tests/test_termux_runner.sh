#!/bin/sh
# shellcheck disable=SC2015  # ok/bad siempre devuelven 0: "A && B || C" es seguro aquí
# Test de install/install-termux.sh (issue #43): cada paso corre en un
# subproceso; un `exit 1` en un paso no crítico no mata el runner, se sigue
# con el resto y se resume al final. Un fallo en un paso crítico aborta.
#
# Monta un repo falso con pasos de mentira (no instala nada).
#
# Uso: sh install/shared/tests/test_termux_runner.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

mk_repo() {
    # mk_repo <dir> <paso_que_falla>
    d="$1"
    mkdir -p "$d/install" "$d/lib" "$d/devices/tel"
    cp "$ROOT/install/install-termux.sh" "$d/install/"
    cp "$ROOT/lib/output.sh" "$d/lib/"
    printf 'gateway:\n  client_key: "k"\n# client_key: "RELLENAR" (comentario, no cuenta)\n' > "$d/devices/tel/config.yaml"
    for s in 01-packages 02-hosts 03-venv 04-oww 05-supervisord 06-configs 07-boot 08-jota-display 99-smoke-test; do
        if [ "$s.sh" = "$2" ]; then
            printf '#!/bin/sh\necho "corre %s"\nexit 1\n' "$s" > "$d/install/$s.sh"
        else
            printf '#!/bin/sh\necho "corre %s"\n' "$s" > "$d/install/$s.sh"
        fi
    done
}

fail=0
ok()  { echo "ok: $1"; }
bad() { echo "FAIL: $1"; fail=1; }

# 1. Falla un paso NO crítico → los siguientes corren, resumen, rc=1.
mk_repo "$TMP/a" 05-supervisord.sh
out=$(bash "$TMP/a/install/install-termux.sh" 2>&1); rc=$?
[ "$rc" -eq 1 ] && ok "no crítico: rc=1" || bad "no crítico: rc=$rc"
printf '%s' "$out" | grep -q "corre 07-boot" && ok "no crítico: siguió hasta 07-boot" || bad "no crítico: no siguió ($out)"
printf '%s' "$out" | grep -q "corre 08-jota-display" && ok "no crítico: llegó al último paso antes de 99" || bad "no crítico: no llegó a 08"
printf '%s' "$out" | grep -q "errores en: 05-supervisord.sh" && ok "no crítico: resumen nombra el paso" || bad "no crítico: sin resumen ($out)"

# 1b. Falla un paso no crítico → NO se ejecuta 99 (reinicia supervisord con
#     algo roto y tumbaría la versión anterior).
mk_repo "$TMP/a2" 07-boot.sh
out=$(bash "$TMP/a2/install/install-termux.sh" 2>&1); rc=$?
[ "$rc" -eq 1 ] && ok "fallo previo: rc=1" || bad "fallo previo: rc=$rc"
printf '%s' "$out" | grep -q "corre 08-jota-display" && ok "fallo previo: sigue con 08" || bad "fallo previo: no siguió"
printf '%s' "$out" | grep -q "corre 99-smoke-test" && bad "fallo previo: ejecutó 99" || ok "fallo previo: omite 99"

# 1c. 03 (venv), 04 (OWW) y 06 (configs) son críticos.
for crit in 03-venv.sh 04-oww.sh 06-configs.sh; do
    mk_repo "$TMP/c_$crit" "$crit"
    out=$(bash "$TMP/c_$crit/install/install-termux.sh" 2>&1); rc=$?
    if [ "$rc" -eq 1 ] && ! printf '%s' "$out" | grep -q "corre 99-smoke-test" \
       && printf '%s' "$out" | grep -q "crítico"; then
        ok "$crit crítico: aborta sin 99"
    else
        bad "$crit no aborta: rc=$rc ($out)"
    fi
done

# 2. Falla el paso crítico → aborta, no corre el resto.
mk_repo "$TMP/b" 01-packages.sh
out=$(bash "$TMP/b/install/install-termux.sh" 2>&1); rc=$?
[ "$rc" -eq 1 ] && ok "crítico: rc=1" || bad "crítico: rc=$rc"
printf '%s' "$out" | grep -q "corre 02-hosts" && bad "crítico: siguió tras el fallo" || ok "crítico: abortó"

# 3. Todo bien → rc=0.
mk_repo "$TMP/c" ninguno
out=$(bash "$TMP/c/install/install-termux.sh" 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "todo ok: rc=0" || bad "todo ok: rc=$rc ($out)"

# 4. Placeholder real (no comentario) → no instala nada.
mk_repo "$TMP/d" ninguno
printf 'gateway:\n  client_key: "RELLENAR"\n' > "$TMP/d/devices/tel/config.yaml"
out=$(bash "$TMP/d/install/install-termux.sh" 2>&1); rc=$?
[ "$rc" -ne 0 ] && ! printf '%s' "$out" | grep -q "corre 01" && ok "placeholder: rechazado" || bad "placeholder: rc=$rc ($out)"

exit $fail
