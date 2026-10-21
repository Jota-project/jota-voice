#!/data/data/com.termux/files/usr/bin/bash
# install-termux.sh — Runner idempotente para setup de jota-voice en Termux.
# Se invoca desde el install.sh raíz (punto de entrada único, issue #42).
#
# Cada paso corre como SUBPROCESO (issue #43): un `exit 1` dentro de un paso
# solo termina ese paso. Si falla un paso crítico se aborta; si falla uno no
# crítico se sigue con el resto y al final se resume qué falló (código de
# salida 1 si falló alguno).

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export REPO_DIR

. "$REPO_DIR/lib/output.sh"
. "$REPO_DIR/lib/secure.sh"

# ── Validación ────────────────────────────────────────────────
# config.yaml en la raíz es un symlink que crea 06-configs.sh; en un teléfono
# recién clonado aún no existe, así que se valida el devices/<id>/config.yaml
# que se va a enlazar.
_resolve_config() {
    if [ -e "$REPO_DIR/config.yaml" ]; then
        echo "$REPO_DIR/config.yaml"
        return 0
    fi
    if [ -n "${DEVICE_ID:-}" ]; then
        echo "$REPO_DIR/devices/$DEVICE_ID/config.yaml"
        return 0
    fi
    set -- "$REPO_DIR"/devices/*/config.yaml
    if [ "$#" -eq 1 ] && [ -f "$1" ]; then
        echo "$1"
        return 0
    fi
    [ "$#" -gt 1 ] && echo "MULTIPLE"
    return 1
}

CFG="$(_resolve_config)"
case "$CFG" in
    MULTIPLE) _fail "Hay varios devices/*/config.yaml. Define DEVICE_ID=<nombre> y reintenta." ;;
    "")       _fail "No hay config. Crea devices/<id>/config.yaml a partir de config.example.yaml (o ejecuta 'jota-voice init' en el Mac)." ;;
esac
[ -f "$CFG" ] || _fail "No existe $CFG."

# Placeholders sin rellenar, solo en líneas que no son comentario (el
# config.example.yaml los menciona en comentarios). Mismos marcadores que
# rechaza client/config.py.
if grep -v '^[[:space:]]*#' "$CFG" | grep -qE 'RELLENAR|YOUR_|CHANGE-ME'; then
    _fail "$CFG tiene campos sin rellenar (RELLENAR / YOUR_ / CHANGE-ME)."
fi

_ok "Configuración válida: ${CFG#"$REPO_DIR"/}"

# El symlink config.yaml → devices/<id>/config.yaml se crea AQUÍ, antes de
# los pasos: 02-hosts.sh (lib/yaml.sh) lee $REPO_DIR/config.yaml y en un
# teléfono recién clonado corría antes de 06, escribiendo un /etc/hosts sin
# las entradas de `hosts:`. 06-configs.sh lo ve ya creado (no-op).
if [ ! -e "$REPO_DIR/config.yaml" ] && [ ! -L "$REPO_DIR/config.yaml" ]; then
    _id="$(basename "$(dirname "$CFG")")"
    jota_valid_id "$_id" || _fail "device id inválido: '$_id' (solo letras, dígitos, _ y -)"
    ln -s "devices/$_id/config.yaml" "$REPO_DIR/config.yaml"
    _ok "symlink creado: config.yaml → devices/$_id/config.yaml"
fi

# ── Ejecutar pasos ────────────────────────────────────────────
# Lista explícita (no un glob de install/*.sh): este runner y otros ficheros
# viven en install/ y no son pasos.
STEPS="01-packages.sh 02-hosts.sh 03-venv.sh 04-oww.sh 05-supervisord.sh 06-configs.sh 07-boot.sh 08-jota-display.sh 99-smoke-test.sh"
# Sin estos no tiene sentido seguir: el resto fallaría en cascada o, peor,
# 99 reiniciaría supervisord con un venv, un OWW o una config rotos y
# tumbaría la versión anterior que seguía funcionando.
CRITICAL="01-packages.sh 03-venv.sh 04-oww.sh 06-configs.sh"
# 99 para y rearranca supervisord: solo si todo lo anterior salió bien.
RESTART_STEP="99-smoke-test.sh"

_failed_steps=""
for name in $STEPS; do
    if [ "$name" = "$RESTART_STEP" ] && [ -n "$_failed_steps" ]; then
        echo ""
        _warn "Se omite $name (reinicia supervisord): fallaron pasos previos y los servicios actuales siguen en marcha."
        continue
    fi
    echo ""
    echo "=== $name ==="
    # bash explícito: los pasos usan `source` (el sh de Termux es dash).
    bash "$REPO_DIR/install/$name"
    rc=$?
    if [ "$rc" -ne 0 ]; then
        _err "$name falló (código: $rc)"
        _failed_steps="$_failed_steps $name"
        case " $CRITICAL " in
            *" $name "*)
                _err "$name es crítico: se aborta la instalación."
                break
                ;;
        esac
    fi
done

echo ""
if [ -z "$_failed_steps" ]; then
    echo "✓ install.sh completado — todos los pasos exitosos"
    exit 0
fi
echo "⚠ install.sh completado con errores en:$_failed_steps"
echo "  Revisa la salida de esos pasos y relánzalos sueltos con:"
echo "    REPO_DIR=$REPO_DIR bash install/<paso>.sh"
exit 1
