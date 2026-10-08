"""Espacio de trabajo guardado en disco: una carpeta por conexión y un archivo .sql por hoja.

    ~/.minisql/sesion/
      indice.json               conexiones, orden y nombre de las hojas, cursor (pequeño)
      DEV/Hoja1.sql             el texto de cada hoja
      DEV/Cierre mensual.sql
      DEV/objetos/SCOTT.PKG.sql código sin compilar de un objeto abierto
      DEV (guardadas)/…         hojas de una conexión cerrada, hasta volver a conectarse

Las hojas pueden ser muy grandes, así que cada .sql se escribe solo cuando cambió. Todo se escribe
de forma atómica (archivo temporal + reemplazo), así que un cierre inesperado nunca deja un archivo
a medias; el índice además conserva su versión anterior en indice.bak.
"""
import contextlib
import json
import os
import re
from pathlib import Path

from .config import SESSION_DIR

INDEX = "indice.json"
GROUPS = ("connections", "parked")         # conexiones abiertas y hojas guardadas de las cerradas
PARKED_SUFFIX = " (guardadas)"
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


# ---------------------------------------------------------------- escritura segura

def write_text_atomic(path, text):
    """Escribe el archivo completo o no lo toca (nunca queda a medias)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_json_atomic(path, data):
    """Como write_text_atomic, y la versión anterior queda en .bak."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=1, ensure_ascii=False))
        f.flush()
        os.fsync(f.fileno())
    if path.exists():
        os.replace(path, path.with_suffix(".bak"))
    os.replace(tmp, path)


def read_json(path):
    """El JSON del archivo, o del .bak si el principal falta o está dañado; None si ninguno sirve."""
    path = Path(path)
    for candidate in (path, path.with_suffix(".bak")):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            continue
    return None


# ---------------------------------------------------------------- nombres de archivo

def safe_name(text, default):
    """Nombre válido para un archivo o carpeta en Windows (el nombre real queda en el índice)."""
    name = _BAD_CHARS.sub("_", text or "").strip().rstrip(".") or default
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    return name[:80]


def unique_name(name, used, suffix=""):
    """name+suffix sin repetir dentro de `used` (sin distinguir mayúsculas, como Windows)."""
    candidate, n = name + suffix, 1
    while candidate.lower() in used:
        n += 1
        candidate = f"{name} ({n}){suffix}"
    used.add(candidate.lower())
    return candidate


def worth_keeping(state):
    """Deja solo las hojas con texto y los objetos con código sin compilar; None si no queda nada."""
    tabs = [t for t in state.get("tabs", [])
            if (t.get("kind") == "sheet" and t.get("text", "").strip())
            or (t.get("kind") == "object" and t.get("code"))]
    return dict(state, tabs=tabs, current=0) if tabs else None


# ---------------------------------------------------------------- almacén

class WorkspaceStore:
    """Lee y escribe el espacio de trabajo. Hacia afuera todo es un dict con el texto de las hojas
    dentro ({"connections": [...], "parked": [...]}); el almacén lo reparte en carpetas y archivos."""

    def __init__(self, root=None, legacy_file=None):
        self.root = Path(root or SESSION_DIR)
        self.legacy_file = Path(legacy_file) if legacy_file else None
        self.written = {}          # ruta relativa -> texto que hay en disco (para no reescribir)
        self.last_index = None
        self.cleaned = False

    # --- leer
    def load(self):
        index = read_json(self.root / INDEX)
        if index is None:
            return self._load_legacy()
        for group in GROUPS:
            for conn in index.get(group, []):
                for tab in conn.get("tabs", []):
                    rel = tab.pop("file", None)
                    if tab.get("kind") == "object":
                        tab.setdefault("code", None)
                    if rel:
                        text = self._read(rel)
                        tab["text" if tab.get("kind") == "sheet" else "code"] = text
                        self.written[rel] = text
        return index

    def _read(self, rel):
        try:
            return (self.root / rel).read_text(encoding="utf-8")
        except OSError:
            return ""

    def _load_legacy(self):
        """Sesión del formato anterior (un solo sesion.json); se pasa al nuevo al guardar."""
        if self.legacy_file is not None:
            return read_json(self.legacy_file) or {}
        return {}

    # --- escribir
    def save(self, data):
        index, files = self._layout(data)
        for rel, text in files.items():                       # 1) solo las hojas que cambiaron
            if self.written.get(rel) != text or not (self.root / rel).exists():
                write_text_atomic(self.root / rel, text)
                self.written[rel] = text
        payload = json.dumps(index, sort_keys=True, ensure_ascii=False)
        if payload != self.last_index:                         # 2) el índice, si cambió
            write_json_atomic(self.root / INDEX, index)
            self.last_index = payload
        self._remove_unused(files)                             # 3) hojas cerradas o renombradas

    def _layout(self, data):
        """Reparte el dict en un índice sin textos y {ruta relativa: texto}."""
        index, files, folders = {"version": 2}, {}, set()
        for group in GROUPS:
            out = []
            for conn in data.get(group, []):
                base = safe_name(conn.get("name"), "Conexion") + (PARKED_SUFFIX if group == "parked" else "")
                folder = unique_name(base, folders)
                names, tabs = set(), []
                for tab in conn.get("tabs", []):
                    entry = {k: v for k, v in tab.items() if k not in ("text", "code")}
                    if tab.get("kind") == "sheet":
                        rel = f"{folder}/{unique_name(safe_name(tab.get('title'), 'Hoja'), names, '.sql')}"
                        files[rel] = tab.get("text", "")
                        entry["file"] = rel
                    elif tab.get("code"):
                        obj = safe_name(f"{tab.get('owner')}.{tab.get('name')}", "objeto")
                        rel = f"{folder}/objetos/{unique_name(obj, names, '.sql')}"
                        files[rel] = tab["code"]
                        entry["file"] = rel
                    tabs.append(entry)
                out.append(dict(conn, tabs=tabs))
            index[group] = out
        return index, files

    def _remove_unused(self, files):
        for rel in [r for r in self.written if r not in files]:
            self._unlink(rel)
            del self.written[rel]
        if not self.cleaned:            # restos de una sesión anterior que ya nadie usa
            self.cleaned = True
            for path in self.root.rglob("*.sql"):
                rel = path.relative_to(self.root).as_posix()
                if rel not in files:
                    self._unlink(rel)
        for folder in sorted((p for p in self.root.rglob("*") if p.is_dir()), reverse=True):
            with contextlib.suppress(OSError):
                folder.rmdir()           # solo si quedó vacía

    def _unlink(self, rel):
        with contextlib.suppress(FileNotFoundError):
            (self.root / rel).unlink()


def load_session(root=None):
    """El espacio de trabajo guardado, con el texto de las hojas dentro."""
    return WorkspaceStore(root).load()
