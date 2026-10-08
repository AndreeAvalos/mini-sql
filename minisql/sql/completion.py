"""Análisis del texto para el autocompletado: qué se está escribiendo, alias de tablas y ranking."""

import re

from .text import IDENT, SQL_KEYWORDS, blank_literals, norm_ident, q

TABLE_NAME = rf'{IDENT}(?:\s*\.\s*{IDENT})?(?![\w$#"]|\s*\.)'   # no toma "hr." a medio escribir


TABLE_REF = re.compile(
    rf"\b(FROM|JOIN|UPDATE|INTO|USING)\b\s*({TABLE_NAME})(?:\s+(?:AS\s+)?({IDENT}))?"
    rf"|,\s*({TABLE_NAME})(?:\s+(?:AS\s+)?({IDENT}))?",
    re.I,
)


CLAUSE = re.compile(r"\b(SELECT|FROM|WHERE|GROUP|ORDER|HAVING|SET|VALUES|ON|JOIN|INTO|CONNECT|START)\b", re.I)


def completion_target(before: str):
    """De lo escrito antes del cursor saca ([calificadores], prefijo).
    'select e.nom' -> (['E'], 'nom');  'hr.emp' -> (['HR'], 'emp');  'hr.emp.' -> (['HR', 'EMP'], '')."""
    prefix = re.search(r"[\w$#]*$", before).group()
    rest = before[:len(before) - len(prefix)]
    quals = []
    while rest.endswith(".") and len(quals) < 2:
        m = re.search(IDENT + r"$", rest[:-1])
        if not m:
            break
        quals.insert(0, norm_ident(m.group()))
        rest = rest[:m.start()]
    return quals, prefix


def last_clause(sql: str, end=None):
    """La última palabra de cláusula (SELECT, FROM, WHERE…) antes de la posición end, o None."""
    matches = list(CLAUSE.finditer(sql, 0, len(sql) if end is None else end))
    return matches[-1].group(1).upper() if matches else None


def table_refs(statement: str):
    """Tablas que usa la sentencia: {ALIAS o NOMBRE: (dueño o None, NOMBRE)}."""
    sql = blank_literals(statement)
    refs, plain = {}, {}
    for m in TABLE_REF.finditer(sql):
        if m.group(1):
            target, alias = m.group(2), m.group(3)
        else:
            if last_clause(sql, m.start()) != "FROM":
                continue                     # una coma fuera del FROM (lista de columnas, etc.)
            target, alias = m.group(4), m.group(5)
        parts = [norm_ident(p) for p in re.findall(IDENT, target)]
        if not parts or parts[-1] in SQL_KEYWORDS:
            continue
        ref = (parts[0], parts[1]) if len(parts) == 2 else (None, parts[0])
        plain[ref[1]] = ref
        if alias and alias.upper() not in SQL_KEYWORDS:
            refs[norm_ident(alias)] = ref
    plain.update(refs)                       # el alias manda sobre un nombre de tabla igual
    return plain


def wants_alias(before: str) -> bool:
    """True si lo que se está escribiendo es una tabla dentro de FROM/JOIN/UPDATE/USING (o tras una coma del FROM)."""
    head = re.sub(rf"(?:{IDENT}\s*\.\s*)*[\w$#]*$", "", blank_literals(before))
    if re.search(r"\b(FROM|JOIN|UPDATE|USING)\s*$", head, re.I):
        return True
    if head.rstrip().endswith(","):
        return last_clause(head) == "FROM"
    return False


def make_alias(name: str, taken) -> str:
    """Alias corto con las iniciales: CLIENTE_DIRECCION -> cd. Evita palabras clave y alias ya usados."""
    parts = [p for p in re.split(r"[_$#\d\s]+", name) if p]
    base = "".join(p[0] for p in parts).lower()[:4] or "t"
    taken = {t.upper() for t in taken}
    alias, n = base, 1
    while alias.upper() in taken or alias.upper() in SQL_KEYWORDS:
        n += 1
        alias = f"{base}{n}"
    return alias


def alias_spelling(statement: str, alias: str) -> str:
    """El alias tal como lo escribió el usuario (para no cambiarle mayúsculas/minúsculas)."""
    if alias != alias.upper():
        return q(alias)                  # alias entre comillas
    m = re.search(rf"(?<![\w$#]){re.escape(alias)}(?![\w$#])", blank_literals(statement), re.I)
    return m.group() if m else alias.lower()


def rank_completions(items, prefix, limit=300):
    """items: [(nombre, detalle, es_identificador, prioridad[, alias])]. Primero los que empiezan
    con el prefijo, luego (con 3+ letras) los que lo contienen; dentro de eso por prioridad y en el
    orden recibido (las columnas quedan en el orden de la tabla). Sin repetidos.
    Devuelve [(nombre, detalle, es_identificador, alias)]; alias es "" si no se debe anteponer."""
    p = prefix.upper()
    scored = []
    for idx, item in enumerate(items):
        name, detail, ident, prio = item[:4]
        qual = item[4] if len(item) > 4 else ""
        n = name.upper()
        if n.startswith(p):
            rank = 0
        elif len(p) >= 3 and p in n:
            rank = 1
        else:
            continue
        scored.append((rank, prio, idx, n, name, detail, ident, qual))
    scored.sort()
    seen, out = set(), []
    for _rank, _prio, _idx, n, name, detail, ident, qual in scored:
        key = (qual.upper(), n)          # e.ID y d.ID son sugerencias distintas
        if key not in seen:
            seen.add(key)
            out.append((name, detail, ident, qual))
            if len(out) >= limit:
                break
    return out
