#!/bin/sh
# python.sh — Elegir el intérprete de Python de los instaladores (issue #45).
#
# El cliente usa asyncio.Task.cancelling() → exige Python >= 3.11.
# En macOS, 01-homebrew.sh instala python@3.12, que es keg-only: el python3
# del PATH puede ser otro (pyenv, Anaconda, Xcode CLT 3.9…). Por eso los
# venvs se crean con un intérprete elegido explícitamente, no con `python3`.

JOTA_PYTHON_MIN_MAJOR=3
JOTA_PYTHON_MIN_MINOR=11

# jota_python_ok <bin> — 0 si <bin> ejecuta y es >= 3.11.
jota_python_ok() {
    [ -n "$1" ] || return 1
    "$1" -c "import sys; sys.exit(0 if sys.version_info >= ($JOTA_PYTHON_MIN_MAJOR, $JOTA_PYTHON_MIN_MINOR) else 1)" >/dev/null 2>&1
}

# jota_python_version <bin> — "3.12.4" o vacío.
jota_python_version() {
    "$1" -c 'import platform; print(platform.python_version())' 2>/dev/null
}

# jota_find_python — imprime la ruta del intérprete a usar, o falla.
# Orden: $JOTA_PYTHON (si se fija, es obligatorio que valga) → python@3.12
# de Homebrew → python3.13/3.12/3.11 → python3 → python (Termux).
jota_find_python() {
    if [ -n "${JOTA_PYTHON:-}" ]; then
        if jota_python_ok "$JOTA_PYTHON"; then
            echo "$JOTA_PYTHON"
            return 0
        fi
        echo "  ✗ JOTA_PYTHON=$JOTA_PYTHON no es un Python >= $JOTA_PYTHON_MIN_MAJOR.$JOTA_PYTHON_MIN_MINOR" >&2
        return 1
    fi
    if command -v brew >/dev/null 2>&1; then
        _jpy_brew="$(brew --prefix python@3.12 2>/dev/null)/bin/python3.12"
        if [ -x "$_jpy_brew" ] && jota_python_ok "$_jpy_brew"; then
            echo "$_jpy_brew"
            return 0
        fi
    fi
    for _jpy in python3.13 python3.12 python3.11 python3 python; do
        _jpy_path="$(command -v "$_jpy" 2>/dev/null)" || continue
        if jota_python_ok "$_jpy_path"; then
            echo "$_jpy_path"
            return 0
        fi
    done
    echo "  ✗ No hay ningún Python >= $JOTA_PYTHON_MIN_MAJOR.$JOTA_PYTHON_MIN_MINOR (el cliente usa asyncio.Task.cancelling())." >&2
    echo "    macOS: brew install python@3.12 · Termux: pkg install python · o exporta JOTA_PYTHON=/ruta/a/python3.x" >&2
    return 1
}
