#!/bin/sh
# secure.sh — Higiene de ficheros con secretos para los instaladores.
#
# jota_secure_perms <repo_dir>
#   Re-aplica 0600 a TODOS los ficheros de secretos de devices/ en cada
#   ejecución (issue #44: el wizard solo lo hacía al crear, y los ficheros
#   acababan en 644 tras ediciones o copias):
#     devices/*/config.yaml   (client_key del gateway)
#     devices/*/.env          (p.ej. cabeceras de Cloudflare Access)
#     devices/*.env           (credenciales SSH de deploy.sh; salvo example.env)
#   Sigue symlinks (config.yaml de la raíz o de Application Support apuntan
#   aquí). Nunca falla: un chmod imposible solo avisa.

jota_secure_perms() {
    _js_repo="$1"
    _js_fixed=0
    for _js_f in "$_js_repo"/devices/*/config.yaml "$_js_repo"/devices/*/.env "$_js_repo"/devices/*.env; do
        [ -f "$_js_f" ] || continue
        case "$_js_f" in */devices/example.env) continue ;; esac
        _js_mode=$(_jota_file_mode "$_js_f") || _js_mode=""
        if [ "$_js_mode" != "600" ]; then
            if chmod 600 "$_js_f" 2>/dev/null; then
                echo "  ✓ permisos ${_js_mode:-?} → 600: ${_js_f#"$_js_repo"/}"
                _js_fixed=$((_js_fixed + 1))
            else
                echo "  ! no se pudo aplicar chmod 600 a ${_js_f#"$_js_repo"/}" >&2
            fi
        fi
    done
    [ "$_js_fixed" -eq 0 ] && echo "  ✓ permisos 600 verificados en devices/"
    return 0
}

# jota_valid_id <id> — 0 si <id> es un nombre de dispositivo seguro para
# usarlo en rutas (devices/<id>/...): letras, dígitos, "_" y "-", 1-64
# caracteres, sin empezar por "-" (issue #55: "../../tmp/x" escapaba de
# devices/).
jota_valid_id() {
    case "$1" in
        ""|-*|*[!A-Za-z0-9_-]*) return 1 ;;
    esac
    [ "${#1}" -le 64 ]
}

# Modo octal (p.ej. "644") de un fichero: stat de GNU/Termux o de BSD (macOS).
# GNU primero: `stat -f` en GNU significa "sistema de ficheros" y escribiría
# basura en stdout; `stat -c` en BSD falla sin escribir nada.
_jota_file_mode() {
    stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null
}
