#!/bin/sh
# output.sh — Helpers de output reutilizables

_ok()   { echo "  ✓ $*"; }
_info() { echo "  → $*"; }
_warn() { echo "  ! $*"; }
_err()  { echo "  ✗ $*" >&2; }
_fail() { echo "  ✗ $*" >&2; exit 1; }