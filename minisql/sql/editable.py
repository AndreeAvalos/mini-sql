"""¿Se pueden editar los resultados de una consulta? Solo si vienen de una sola tabla (sin JOIN, GROUP BY,
DISTINCT, UNION…), porque así cada fila del resultado es una fila real de la tabla y se identifica por su
ROWID. Texto puro: la tabla se resuelve y valida después contra el diccionario de Oracle."""
import re
from dataclasses import dataclass, field

from .text import IDENT, blank_literals, norm_ident, strip_comments

ROWID_COLUMN = "MINISQL$ROWID"       # columna oculta con el ROWID de cada fila

# Lo que hace que una fila del resultado no sea una fila de la tabla
_NOT_EDITABLE = [
    (re.compile(r"\bUNION\b|\bINTERSECT\b|\bMINUS\b|\bEXCEPT\b", re.I), "combina consultas (UNION/MINUS…)"),
    (re.compile(r"\bGROUP\s+BY\b|\bHAVING\b", re.I), "agrupa filas (GROUP BY)"),
    (re.compile(r"\bCONNECT\s+BY\b|\bSTART\s+WITH\b|\bPIVOT\b|\bUNPIVOT\b|\bMODEL\b", re.I),
     "usa CONNECT BY, PIVOT o MODEL"),
    (re.compile(r"\bJOIN\b", re.I), "tiene JOIN"),
    (re.compile(r"\bFOR\s+UPDATE\b", re.I), "tiene FOR UPDATE"),
]
_FROM_TAIL = re.compile(r"\b(WHERE|ORDER|FETCH|OFFSET|FOR|SAMPLE|AS\s+OF|VERSIONS)\b", re.I)


@dataclass
class EditTarget:
    """Tabla de la consulta y qué columnas del resultado se pueden editar."""
    owner: str | None              # None: el esquema del usuario (o un sinónimo, se resuelve después)
    table: str
    star: bool = False             # SELECT * / t.*: todas las columnas de la tabla
    sources: dict = field(default_factory=dict)      # nombre en el resultado -> columna de la tabla
    readonly: set = field(default_factory=set)       # nombres de expresiones (no editables)

    def source_of(self, result_name, table_columns):
        """Columna de la tabla que muestra la columna `result_name` del resultado, o None."""
        if result_name in self.readonly:
            return None
        if result_name in self.sources:
            return self.sources[result_name]
        if self.star and result_name in table_columns:
            return result_name
        return None


def _top_level(text):
    """Texto con lo que está entre paréntesis vaciado (subqueries, llamadas a funciones)."""
    out, depth = [], 0
    for ch in text:
        if ch == "(":
            depth += 1
            out.append("(")
        elif ch == ")":
            depth = max(0, depth - 1)
            out.append(")")
        else:
            out.append(ch if depth == 0 else " ")
    return "".join(out)


def _split_top_commas(text):
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts]


_PLAIN_ITEM = re.compile(rf"^(?:{IDENT}\s*\.\s*)?({IDENT})(?:\s+(?:AS\s+)?({IDENT}))?$", re.I)
_STAR_ITEM = re.compile(rf"^(?:{IDENT}\s*\.\s*)?\*$")
_EXPR_ALIAS = re.compile(rf"\s(?:AS\s+)?({IDENT})$", re.I)


def editable_target(sql):
    """(EditTarget, None) si los resultados se pueden editar, o (None, motivo en español)."""
    text = blank_literals(strip_comments(sql)).strip().rstrip(";").strip()
    if not re.match(r"SELECT\b", text, re.I):
        return None, "no es un SELECT"
    flat = _top_level(text)
    for rx, reason in _NOT_EDITABLE:
        if rx.search(flat):
            return None, reason
    m = re.search(r"\bFROM\b", flat, re.I)
    if not m:
        return None, "no tiene FROM"
    select_list = text[len("SELECT"):m.start()].strip()
    if re.match(r"(DISTINCT|UNIQUE)\b", select_list, re.I):
        return None, "usa DISTINCT"
    from_part = flat[m.end():]
    tail = _FROM_TAIL.search(from_part)
    tables = from_part[:tail.start()] if tail else from_part
    if "," in tables:
        return None, "consulta varias tablas"
    tm = re.fullmatch(rf"\s*({IDENT})(?:\s*\.\s*({IDENT}))?(?:\s+({IDENT}))?\s*", tables)
    if not tm or "@" in tables:
        return None, "no consulta una tabla directamente"
    if tm.group(2):
        owner, table = norm_ident(tm.group(1)), norm_ident(tm.group(2))
    else:
        owner, table = None, norm_ident(tm.group(1))
    if table == "DUAL":
        return None, "consulta DUAL"
    target = EditTarget(owner, table)
    for item in _split_top_commas(select_list):
        if _STAR_ITEM.match(item):
            target.star = True
            continue
        plain = _PLAIN_ITEM.match(item)
        if plain:
            source = norm_ident(plain.group(1))
            if source == "ROWNUM" or source == "ROWID" or source == "LEVEL":
                target.readonly.add(norm_ident(plain.group(2) or plain.group(1)))
                continue
            target.sources[norm_ident(plain.group(2) or plain.group(1))] = source
            continue
        alias = _EXPR_ALIAS.search(item)      # una expresión: con alias se marca solo lectura
        if alias and not item.rstrip().endswith(")"):
            target.readonly.add(norm_ident(alias.group(1)))
        else:
            target.readonly.add(" ".join(item.upper().split()))
    return target, None


def rowid_query(sql):
    """La consulta del usuario, con el ROWID de cada fila como primera columna (oculta en la grilla).
    Oracle solo lo permite si la consulta es de una tabla (si no, da ORA-01446 y no se edita)."""
    body = sql.strip().rstrip(";").rstrip()
    return f'SELECT ROWIDTOCHAR(ROWID) AS "{ROWID_COLUMN}", q.* FROM (\n{body}\n) q'
