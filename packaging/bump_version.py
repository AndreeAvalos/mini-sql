"""Decide la versión a publicar a partir de los mensajes de commit (lo usa .github/workflows/version.yml).

    [CI]        sube el último número   0.2.0 → 0.2.1
    [CI minor]  sube el del medio       0.2.1 → 0.3.0
    [CI major]  sube el primero         0.3.0 → 1.0.0

Si la versión que está en minisql/__init__.py todavía no tiene etiqueta (nadie la publicó), se publica tal
cual: así no se salta un número que alguien subió a mano. Sin [CI] en ningún mensaje, no hace nada.

    python packaging/bump_version.py      # lee MESSAGES y TAGS del entorno; escribe en GITHUB_OUTPUT
"""
import os
import re
import sys
from pathlib import Path

INIT = Path(__file__).resolve().parents[1] / "minisql" / "__init__.py"
_ASSIGN = re.compile(r'(__version__\s*=\s*")([^"]+)(")')
_MARK = re.compile(r"\[CI(?:\s+(minor|major|patch))?\]", re.I)
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(-.+)?")
_ORDER = {"patch": 0, "minor": 1, "major": 2}


def level(messages):
    """La subida más grande pedida en los mensajes ("patch", "minor", "major"), o None si ninguno dice [CI]."""
    found = [(m.group(1) or "patch").lower() for m in _MARK.finditer(messages or "")]
    return max(found, key=_ORDER.get) if found else None


def bump(version, how):
    major, minor, patch, pre = _VERSION.fullmatch(version).groups()
    if how == "major":
        return f"{int(major) + 1}.0.0"
    if how == "minor":
        return f"{major}.{int(minor) + 1}.0"
    if pre:                                    # 0.3.0-beta.2 + [CI] → 0.3.0 (sale la versión final)
        return f"{major}.{minor}.{patch}"
    return f"{major}.{minor}.{int(patch) + 1}"


def next_version(current, how, tags):
    """La versión a publicar: la actual si nunca se publicó; si ya tiene etiqueta, la siguiente."""
    if f"v{current}" not in tags:
        return current
    new = bump(current, how)
    while f"v{new}" in tags:                   # por si alguien ya publicó esa a mano
        new = bump(new, "patch")
    return new


def main():
    how = level(os.environ.get("MESSAGES", ""))
    out = {"publish": "false"}
    if how:
        text = INIT.read_text(encoding="utf-8")
        current = _ASSIGN.search(text).group(2)
        tags = set(os.environ.get("TAGS", "").split())
        new = next_version(current, how, tags)
        if new != current:
            INIT.write_text(_ASSIGN.sub(lambda m: f"{m.group(1)}{new}{m.group(3)}", text, count=1), encoding="utf-8")
        out = {"publish": "true", "version": new, "tag": f"v{new}", "changed": str(new != current).lower()}
        print(f"[CI {how}]: {current} → {new}")
    else:
        print("Ningún commit dice [CI]: no se publica nada")
    target = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{k}={v}\n" for k, v in out.items())
    if target:
        with open(target, "a", encoding="utf-8") as f:
            f.write(lines)
    else:
        sys.stdout.write(lines)


if __name__ == "__main__":
    main()
