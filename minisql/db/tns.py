"""Lectura de tnsnames.ora."""

import os
import re
from pathlib import Path

from ..config import load_settings


def default_tns_path():
    """Ruta guardada por el usuario; si no hay, intenta con TNS_ADMIN."""
    saved = load_settings().get("tns_path")
    if saved:
        return saved
    tns_admin = os.environ.get("TNS_ADMIN")
    if tns_admin and (Path(tns_admin) / "tnsnames.ora").is_file():
        return str(Path(tns_admin) / "tnsnames.ora")
    return ""


def parse_tnsnames(path, _seen=None):
    """Lee un tnsnames.ora y devuelve {ALIAS: descriptor}. Soporta IFILE y alias múltiples (A, B = ...)."""
    path = Path(path)
    _seen = _seen or set()
    if path.resolve() in _seen:
        return {}
    _seen.add(path.resolve())

    raw = path.read_text(encoding="utf-8", errors="replace")
    text = "\n".join(line.split("#", 1)[0] for line in raw.splitlines())
    entries, i, n = {}, 0, len(text)
    while i < n:
        eq = text.find("=", i)
        if eq < 0:
            break
        names = text[i:eq].strip()
        par = text.find("(", eq)
        line_end = text.find("\n", eq)
        line_end = n if line_end < 0 else line_end
        # Valor sin paréntesis en la misma línea, p. ej. IFILE = otra_ruta.ora
        if par < 0 or (par > line_end and text[eq + 1:line_end].strip()):
            value = text[eq + 1:line_end].strip().strip('"')
            if names.upper() == "IFILE" and value:
                inc = Path(value) if Path(value).is_absolute() else path.parent / value
                if inc.is_file():
                    entries.update(parse_tnsnames(inc, _seen))
            i = line_end + 1
            continue
        depth, k = 0, par
        while k < n:
            if text[k] == "(":
                depth += 1
            elif text[k] == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        descriptor = re.sub(r"\s+", " ", text[par:k + 1]).strip()
        for name in names.split(","):
            name = name.strip()
            if name:
                entries[name.upper()] = descriptor
        i = k + 1
    return entries
