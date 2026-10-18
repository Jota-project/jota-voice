#!/usr/bin/env python3
"""Valida los token files contra las reglas que impone Penpot al importar.

Regla del error real de Penpot:
  "Los nombres de token solo pueden contener letras y dígitos
   separados por caracteres . y no pueden empezar con un signo $"
"""
import json, re, sys, glob

SEG = re.compile(r'^[A-Za-z0-9]+$')
nombres, alias, errores = set(), [], []

def walk(node, path, archivo):
    if isinstance(node, dict):
        if '$value' in node:
            full = '.'.join(path)
            nombres.add(full)
            for seg in path:
                if not SEG.match(seg):
                    errores.append(f"{archivo}: segmento inválido '{seg}' en '{full}' (solo letras y dígitos)")
            if full.startswith('$'):
                errores.append(f"{archivo}: '{full}' empieza por $")
            v = node.get('$value')
            if isinstance(v, str):
                for m in re.findall(r'\{([^}]+)\}', v):
                    alias.append((m, full, archivo))
            return
        for k, sub in node.items():
            if k.startswith('$'):
                continue
            walk(sub, path + [k], archivo)

archivos = sorted(glob.glob('*.tokens.json'))
for f in archivos:
    try:
        data = json.load(open(f))
    except json.JSONDecodeError as e:
        errores.append(f"{f}: JSON inválido — {e}")
        continue
    for setname, content in data.items():
        if not SEG.match(setname):
            errores.append(f"{f}: nombre de SET inválido '{setname}' — sin barras ni guiones")
        walk(content, [setname], f)

rotos = [(a, o, f) for a, o, f in alias if a not in nombres]
for a, o, f in rotos:
    errores.append(f"{f}: alias roto en '{o}' → {{{a}}}")

print(f"archivos:   {', '.join(archivos)}")
print(f"tokens:     {len(nombres)}")
print(f"alias:      {len(alias)}")
print()
if errores:
    print(f"❌ {len(errores)} problema(s):")
    for e in errores: print("   ·", e)
    sys.exit(1)
print("✅ todos los nombres y alias cumplen las reglas de Penpot")
