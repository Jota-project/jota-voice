#!/bin/sh
# Test de lib/python.sh (issue #45): los instaladores eligen un Python
# >= 3.11 explícito en vez del python3 genérico del PATH.
#
# Usa intérpretes falsos (scripts que responden al check de versión con su
# código de salida), así que no depende del Python instalado.
#
# Uso: sh install/shared/tests/test_python_select.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
. "$ROOT/lib/python.sh"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# fake <dir> <nombre> <ok|old>: "ok" pasa el check (>= 3.11), "old" no.
# shellcheck disable=SC2016  # el $2 es del script falso, no de este
fake() {
    mkdir -p "$1"
    if [ "$3" = ok ]; then
        printf '#!/bin/sh\ncase "$2" in *python_version*) echo 3.12.4 ;; esac\nexit 0\n' > "$1/$2"
    else
        printf '#!/bin/sh\ncase "$2" in *python_version*) echo 3.9.6; exit 0 ;; esac\nexit 1\n' > "$1/$2"
    fi
    chmod +x "$1/$2"
}

fail=0
ok()  { echo "ok: $1"; }
bad() { echo "FAIL: $1"; fail=1; }

fake "$TMP/a" python3 old
fake "$TMP/a" python3.12 ok
fake "$TMP/b" python3 old

if jota_python_ok "$TMP/a/python3.12"; then ok "3.12 aceptado"; else bad "3.12 rechazado"; fi
if jota_python_ok "$TMP/a/python3"; then bad "3.9 aceptado"; else ok "3.9 rechazado"; fi
if jota_python_ok "$TMP/no-existe"; then bad "inexistente aceptado"; else ok "inexistente rechazado"; fi

# python3 del PATH es viejo pero hay python3.12 → elige python3.12.
got=$(PATH="$TMP/a" JOTA_PYTHON="" jota_find_python 2>/dev/null)
if [ "$got" = "$TMP/a/python3.12" ]; then ok "prefiere python3.12 al python3 viejo"; else bad "eligió '$got'"; fi

# Solo hay un python3 viejo → falla con mensaje.
if out=$(PATH="$TMP/b" JOTA_PYTHON="" jota_find_python 2>&1); then
    bad "aceptó un PATH sin Python >= 3.11 ($out)"
else
    case "$out" in *3.11*) ok "falla con mensaje claro" ;; *) bad "mensaje: $out" ;; esac
fi

# JOTA_PYTHON manda y, si no vale, es error (no se ignora en silencio).
got=$(PATH="$TMP/b" JOTA_PYTHON="$TMP/a/python3.12" jota_find_python 2>/dev/null)
if [ "$got" = "$TMP/a/python3.12" ]; then ok "JOTA_PYTHON respetado"; else bad "JOTA_PYTHON ignorado ('$got')"; fi
# Subshell: en sh POSIX una asignación delante de una función persiste.
if (PATH="$TMP/a" JOTA_PYTHON="$TMP/b/python3" jota_find_python >/dev/null 2>&1); then
    bad "JOTA_PYTHON viejo aceptado"
else
    ok "JOTA_PYTHON viejo rechazado"
fi

exit $fail
