"""Código PL/SQL como texto: unidades separadas por '/', líneas y plantillas de depuración."""

import re

from .text import IDENT, SQL_FUNCTIONS, SQL_KEYWORDS, blank_literals, norm_ident, q

UNIT_HEADER = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:NON)?EDITIONABLE\s+)?"
    r"(PACKAGE\s+BODY|TYPE\s+BODY|PACKAGE|TYPE|PROCEDURE|FUNCTION|TRIGGER)\s+"
    rf"({IDENT})(?:\s*\.\s*({IDENT}))?",
    re.I,
)


# Tipo de unidad -> tipo de objeto que abre el visor
UNIT_OBJECT = {"PACKAGE BODY": "PACKAGE", "TYPE BODY": "TYPE"}


def split_units(code: str):
    """Separa el código en unidades terminadas por una línea con '/'.
    Devuelve [(línea donde empieza, texto)]; las líneas cuentan desde 1."""
    units, buf, start = [], [], None
    for i, line in enumerate(code.split("\n"), 1):
        if line.strip() == "/":
            if buf:
                units.append((start, "\n".join(buf).rstrip()))
            buf, start = [], None
            continue
        if start is None:
            if not line.strip():
                continue
            start = i
        buf.append(line)
    if buf and "\n".join(buf).strip():
        units.append((start, "\n".join(buf).rstrip()))
    return units


def unit_info(text: str, default_owner: str):
    """('PACKAGE BODY', dueño, nombre) de una unidad 'CREATE OR REPLACE ...', o None."""
    m = UNIT_HEADER.match(text)
    if not m:
        return None
    kind = re.sub(r"\s+", " ", m.group(1).upper())
    if m.group(3):
        return kind, norm_ident(m.group(2)), norm_ident(m.group(3))
    return kind, default_owner, norm_ident(m.group(2))


def unit_at_line(code: str, line: int, default_owner: str):
    """Para una línea del editor: (tipo, dueño, nombre, línea dentro de la unidad), o None."""
    found = None
    for start, text in split_units(code):
        if start <= line < start + text.count("\n") + 1:
            found = (start, text)
    if not found:
        return None
    info = unit_info(found[1], default_owner)
    return (*info, line - found[0] + 1) if info else None


def editor_line(code: str, kind: str, name: str, line: int, default_owner: str):
    """Inverso de unit_at_line: la línea del editor para (tipo, nombre, línea de la unidad), o None."""
    for start, text in split_units(code):
        info = unit_info(text, default_owner)
        if info and info[0] == kind and info[2] == name:
            return start + line - 1
    return None


def map_errors(code, errors, owner):
    """[(tipo, línea, columna, texto)] de ALL_ERRORS -> [(línea del editor, columna, texto)]."""
    names = {unit_info(t, owner)[2] for _s, t in split_units(code) if unit_info(t, owner)}
    name = next(iter(names), "")
    out = []
    for kind, line, pos, text in errors:
        ed = editor_line(code, kind, name, line, owner) if name else None
        out.append((ed or line, pos, text.strip()))
    return out


def plsql_type(data_type, type_owner=None, type_name=None, type_subname=None):
    """Tipo para declarar una variable que reciba un argumento (ALL_ARGUMENTS)."""
    if data_type in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR", "RAW", "VARCHAR"):
        return f"{data_type}(32767)" if data_type != "RAW" else "RAW(2000)"
    if data_type == "PL/SQL BOOLEAN":
        return "BOOLEAN"
    if data_type == "REF CURSOR":
        return "SYS_REFCURSOR"
    if type_name:
        return ".".join(q(p) for p in (type_owner, type_name, type_subname) if p)
    return data_type or "VARCHAR2(32767)"


def debug_wrapper(block: str) -> str:
    """Envuelve el bloque de prueba para que la sesión salga del modo de depuración dentro de la misma
    llamada, termine bien, con error o detenida. Un DEBUG_OFF en una llamada aparte se quedaría esperando
    a un depurador que ya se fue, y la sesión principal quedaría bloqueada."""
    body = "\n".join("    " + line if line.strip() else "" for line in block.strip().split("\n"))
    if not body.rstrip().endswith(";"):
        body += ";"
    return ("BEGIN\n  BEGIN\n" + body + "\n  EXCEPTION WHEN OTHERS THEN\n    DBMS_DEBUG.DEBUG_OFF;\n"
            "    RAISE;\n  END;\n  DBMS_DEBUG.DEBUG_OFF;\nEND;")


def backtrace_line(backtrace: str):
    """(línea, texto) del marco actual en la pila de DBMS_DEBUG, p. ej. "[Line 15]  RETURN X;", o None."""
    m = re.match(r"\s*\[Line (\d+)\]\s*(.*)", backtrace or "")
    return (int(m.group(1)), m.group(2).strip()) if m else None


def same_code(a: str, b: str) -> bool:
    """True si dos líneas de código son la misma sin importar espacios (la pila puede recortarlas)."""
    a, b = " ".join(a.split()), " ".join(b.split())
    return bool(a) and bool(b) and (a == b or a.startswith(b) or b.startswith(a))


def debug_template(owner, package, subprogram, args):
    """Bloque anónimo para llamar al subprograma.
    args: [(argumento, posición, modo, data_type, type_owner, type_name, type_subname)] de una sobrecarga."""
    decls, binds, result = [], [], None
    for arg, pos, mode, dtype, t_owner, t_name, t_sub in args:
        if pos == 0 and arg is None:
            result = plsql_type(dtype, t_owner, t_name, t_sub)
            continue
        if arg is None:
            continue                         # procedimiento sin argumentos
        decls.append(f"  {q(arg)} {plsql_type(dtype, t_owner, t_name, t_sub)} := NULL;  -- {mode}")
        binds.append(f"{q(arg)} => {q(arg)}")
    target = ".".join(q(p) for p in (owner, package, subprogram) if p)
    call = f"{target}({', '.join(binds)})" if binds else target
    if result:
        decls.append(f"  v_resultado {result};")
        call = f"v_resultado := {call}"
    head = "DECLARE\n" + "\n".join(decls) + "\n" if decls else ""
    return f"{head}BEGIN\n  {call};\nEND;"


def variable_candidates(source: str, limit=150):
    """Nombres que podrían ser variables en el código (para pedir su valor al depurador)."""
    seen, out = set(), []
    for w in re.findall(r"[A-Za-z][\w$#]*", blank_literals(source)):
        w = w.upper()
        if w in seen or w in SQL_KEYWORDS or w in SQL_FUNCTIONS:
            continue
        seen.add(w)
        out.append(w)
    return out[:limit]
