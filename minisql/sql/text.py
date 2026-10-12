"""Utilidades de texto SQL: comentarios, sentencias, identificadores y formato.
No dependen de Oracle ni de Qt."""

import re

try:
    import sqlparse  # formateo de SQL (Ctrl+Shift+F)
except ImportError:
    sqlparse = None


DML = {"INSERT", "UPDATE", "DELETE", "MERGE"}


DDL = {"CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME", "GRANT", "REVOKE", "COMMENT"}


PLSQL_START = re.compile(
    r"^\s*(BEGIN|DECLARE|CREATE\s+(OR\s+REPLACE\s+)?((NON)?EDITIONABLE\s+)?"
    r"(PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE))\b",
    re.I,
)


def strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql.strip()


def first_keyword(sql: str) -> str:
    m = re.match(r"\s*(\w+)", strip_comments(sql))
    return m.group(1).upper() if m else ""


def clean_statement(sql: str) -> str:
    """Quita el ';' final en SQL normal, o el '/' final en bloques PL/SQL."""
    sql = sql.strip()
    if PLSQL_START.match(strip_comments(sql)):
        return re.sub(r"\n\s*/\s*$", "", sql).rstrip()
    return sql.rstrip().rstrip(";").rstrip()


def statement_range(text: str, pos: int):
    """Devuelve (inicio, fin) del bloque (separado por líneas en blanco) donde está el cursor."""
    blocks, start = [], 0
    for m in re.finditer(r"\n[ \t]*\n", text):
        blocks.append((start, m.start()))
        start = m.end()
    blocks.append((start, len(text)))
    chosen = (0, 0)
    for s, e in blocks:
        if s <= pos:
            chosen = (s, e)
    return chosen


def statement_at_cursor(text: str, pos: int) -> str:
    """Devuelve el bloque de texto (separado por líneas en blanco) donde está el cursor."""
    s, e = statement_range(text, pos)
    return text[s:e]


def format_sql(sql: str) -> str:
    """Formatea una sentencia: palabras clave en mayúsculas y, si no es PL/SQL, reindentada.
    Nunca deja líneas en blanco dentro, porque separan sentencias en el editor."""
    if sqlparse is None:
        raise RuntimeError("Para formatear instala sqlparse:  pip install sqlparse")
    plsql = bool(PLSQL_START.match(strip_comments(sql)))
    out = sqlparse.format(sql, keyword_case="upper", reindent=not plsql,
                          indent_width=4, use_space_around_operators=not plsql)
    return re.sub(r"\n[ \t]*(?=\n)", "", out).strip()


SQL_KEYWORDS = set("""
ADD ALL ALTER AND ANY AS ASC BEGIN BETWEEN BODY BULK BY CASE CHECK COLLECT COLUMN COMMENT COMMIT
CONNECT CONSTRAINT CREATE CROSS CURSOR DECLARE DEFAULT DELETE DESC DISTINCT DROP EACH ELSE ELSIF
END EXCEPTION EXECUTE EXISTS EXIT FETCH FOR FOREIGN FROM FULL FUNCTION GRANT GROUP HAVING IF
IMMEDIATE IN INDEX INNER INSERT INTERSECT INTO IS JOIN KEY LEFT LIKE LIMIT LOOP MATCHED MERGE MINUS
NOCOPY NOT NULL NULLS OF OFFSET ON OR ORDER OUT OUTER OVER PACKAGE PARTITION PRIMARY PRIOR
PROCEDURE RAISE RECORD REFERENCES REPLACE RETURN RETURNING REVOKE RIGHT ROLLBACK ROWS ROWTYPE
SAVEPOINT SELECT SET START TABLE THEN TO TRIGGER TRUNCATE TYPE UNION UNIQUE UPDATE USING VALUES
VIEW WHEN WHERE WHILE WITH FIRST LAST ONLY NEXT ROW
NUMBER VARCHAR2 NVARCHAR2 CHAR NCHAR DATE TIMESTAMP CLOB BLOB INTEGER BOOLEAN PLS_INTEGER
BINARY_INTEGER RAW LONG INTERVAL
""".split())


SQL_FUNCTIONS = set("""
ABS AVG CAST CEIL COALESCE CONCAT COUNT DECODE DENSE_RANK EXTRACT FLOOR GREATEST INSTR LAG
LEAD LEAST LENGTH LISTAGG LOWER LPAD LTRIM MAX MIN MOD MONTHS_BETWEEN NULLIF NVL NVL2 RANK
REGEXP_INSTR REGEXP_LIKE REGEXP_REPLACE REGEXP_SUBSTR REPLACE ROUND ROW_NUMBER RPAD RTRIM
SIGN SUBSTR SUM SYSDATE SYSTIMESTAMP TO_CHAR TO_DATE TO_NUMBER TO_TIMESTAMP TRIM TRUNC UPPER
ADD_MONTHS LAST_DAY
""".split())


IDENT = r'(?:"[^"\n]+"|[A-Za-z_][\w$#]*)'


LITERAL_START = re.compile(r"--|/\*|'")


def norm_ident(name: str) -> str:
    """Nombre como lo guarda Oracle: sin comillas tal cual, sin comillas en mayúsculas."""
    name = name.strip()
    return name[1:-1] if name.startswith('"') else name.upper()


def string_end(text: str, i: int) -> int:
    """Posición después de la comilla que cierra la cadena que empieza antes de i ('' es una
    comilla escapada), o -1 si la cadena sigue abierta."""
    while True:
        j = text.find("'", i)
        if j < 0:
            return -1
        if text[j + 1:j + 2] == "'":
            i = j + 2
            continue
        return j + 1


def inside_literal(text: str) -> bool:
    """True si el final del texto queda dentro de una cadena o un comentario."""
    i = 0
    while True:
        m = LITERAL_START.search(text, i)
        if not m:
            return False
        if m.group() == "--":
            j = text.find("\n", m.end())
        elif m.group() == "/*":
            j = text.find("*/", m.end())
            j = j + 2 if j >= 0 else -1
        else:
            j = string_end(text, m.end())
        if j < 0:
            return True
        i = j


def blank_literals(sql: str) -> str:
    """Quita comentarios y vacía las cadenas, para analizar la sentencia sin falsos positivos."""
    sql = re.sub(r"/\*.*?\*/|--[^\n]*", " ", sql, flags=re.S)
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


_SLASH_LINE = re.compile(r"[ \t]*/[ \t]*(?:\n|$)")       # una '/' sola en su línea (fin de bloque PL/SQL)
_Q_CLOSE = {"[": "]", "(": ")", "{": "}", "<": ">"}


def _skip_noise(sql: str, i: int) -> int:
    """Posición del primer carácter de código desde i (salta espacios y comentarios)."""
    n = len(sql)
    while i < n:
        if sql[i].isspace():
            i += 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j + 1
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            break
    return i


def _literal_end(sql: str, j: int):
    """Si en j empieza un comentario, una cadena ('…', q'[…]') o un identificador entre comillas, devuelve
    la posición donde termina; si no, None."""
    n = len(sql)
    if sql.startswith("--", j):
        k = sql.find("\n", j)
        return n if k < 0 else k
    if sql.startswith("/*", j):
        k = sql.find("*/", j + 2)
        return n if k < 0 else k + 2
    c = sql[j]
    if c in "qQ" and sql[j + 1:j + 2] == "'" and j + 2 < n and (j == 0 or not (sql[j - 1].isalnum() or
                                                                         sql[j - 1] in "_$#")):
        close = _Q_CLOSE.get(sql[j + 2], sql[j + 2]) + "'"
        k = sql.find(close, j + 3)
        return n if k < 0 else k + 2
    if c == "'":
        k = string_end(sql, j + 1)
        return n if k < 0 else k
    if c == '"':
        k = sql.find('"', j + 1)
        return n if k < 0 else k + 1
    return None


def split_statements(sql: str):
    """Separa un script en sentencias, como SQL*Plus: SQL termina en ';' y los bloques PL/SQL (BEGIN,
    DECLARE, CREATE PROCEDURE…) en una '/' sola en su línea o al final del texto. Ignora ';' dentro de
    cadenas, comentarios e identificadores entre comillas. Devuelve las sentencias sin el ';' final del SQL
    (los bloques PL/SQL conservan su 'END;') y sin los trozos que solo tienen comentarios."""
    out, i, n = [], 0, len(sql)
    while True:
        start = _skip_noise(sql, i)
        if start >= n:
            return out
        plsql = bool(PLSQL_START.match(sql[start:start + 300]))
        j, end, nxt = start, n, n
        while j < n:
            skip = _literal_end(sql, j)
            if skip is not None:
                j = skip
                continue
            if j > start and sql[j - 1] == "\n" and (m := _SLASH_LINE.match(sql, j)):
                end, nxt = j, m.end()
                break
            if sql[j] == ";" and not plsql:
                end, nxt = j, j + 1
                break
            j += 1
        stmt = sql[start:end].strip()
        if stmt and stmt != "/":
            out.append(stmt)
        i = nxt


def q(name):
    """Pone comillas al identificador solo si hace falta."""
    return name if re.fullmatch(r"[A-Z][A-Z0-9_$#]*", name) else f'"{name}"'


_SERVEROUTPUT = re.compile(r"^\s*SET\s+SERVEROUT(?:PUT)?\s+(ON|OFF)\b[^\n]*(?:\n|$)", re.I)
_EXEC = re.compile(r"^\s*EXEC(?:UTE)?\s+(?!IMMEDIATE\b)(.*?)\s*;?\s*$", re.I | re.S)


def sqlplus_to_sql(sql: str):
    """Acepta lo que se escribe en SQL*Plus / SQL Developer y no es SQL:
    SET SERVEROUTPUT ON|OFF (al inicio) y EXEC proc(...) (se vuelve un bloque BEGIN … END;).
    Devuelve (serveroutput: True/False o None si no se indicó, sentencia que queda por ejecutar)."""
    serveroutput = None
    while True:
        m = _SERVEROUTPUT.match(sql)
        if not m:
            break
        serveroutput = m.group(1).upper() == "ON"
        sql = sql[m.end():]
    m = _EXEC.match(sql)
    if m:
        sql = f"BEGIN\n  {m.group(1)};\nEND;"
    return serveroutput, sql.strip()


_SERVEROUTPUT_LINE = re.compile(r"^[ \t]*SET\s+SERVEROUT(?:PUT)?\s+(ON|OFF)\b[^\n]*(?:\n|$)", re.I | re.M)


def split_script(sql: str):
    """Lo que hay que ejecutar de un texto con una o varias sentencias.
    Devuelve (serveroutput: True/False o None, [sentencias]); acepta SET SERVEROUTPUT y EXEC de SQL*Plus."""
    serveroutput = None
    for m in _SERVEROUTPUT_LINE.finditer(sql):
        serveroutput = m.group(1).upper() == "ON"
    statements = []
    for stmt in split_statements(_SERVEROUTPUT_LINE.sub("", sql)):
        stmt = sqlplus_to_sql(stmt)[1]
        if strip_comments(stmt):
            statements.append(stmt)
    return serveroutput, statements


def word_at(line: str, col: int) -> str:
    """El identificador que contiene la columna col (0 = primer carácter) o ''."""
    for m in re.finditer(r"[A-Za-z_][\w$#]*", line):
        if m.start() <= col < m.end():
            return m.group()
    return ""


_CHANGE_KW = re.compile(r"\b(DELETE|UPDATE)\b", re.I)
_WHERE_OR_PAREN = re.compile(r"\(|\)|\bWHERE\b", re.I)
# Palabras que, justo antes de DELETE/UPDATE, indican que no es una sentencia que borre o cambie filas:
# FOR UPDATE, ON DELETE CASCADE, BEFORE/AFTER … OR UPDATE OF (triggers), INSTEAD OF DELETE…
_NOT_A_CHANGE_AFTER = {"FOR", "ON", "BEFORE", "AFTER", "OR", "OF", "INSTEAD", "EACH"}


def changes_without_where(sql: str):
    """DELETE y UPDATE que afectan TODAS las filas porque no tienen WHERE propio. Revisa también los que
    están dentro de bloques PL/SQL (BEGIN … END) y no se deja engañar por un WHERE dentro de un subquery,
    un comentario o un texto. Devuelve un resumen corto de cada uno (para la confirmación)."""
    found = []
    for piece in blank_literals(sql).split(";"):
        if re.search(r"\bMERGE\b", piece, re.I):
            continue                                     # MERGE … THEN UPDATE solo toca las filas que cruzan
        for m in _CHANGE_KW.finditer(piece):
            before = re.findall(r"[A-Za-z_][\w$#]*", piece[:m.start()])
            if before and before[-1].upper() in _NOT_A_CHANGE_AFTER:
                continue
            if not _has_own_where(piece[m.end():]):
                found.append(" ".join(piece[m.start():].split())[:80])
    return found


def _has_own_where(rest: str) -> bool:
    """¿Hay un WHERE al mismo nivel de paréntesis que la sentencia (no dentro de un subquery)?"""
    depth = 0
    for tok in _WHERE_OR_PAREN.finditer(rest):
        if tok.group() == "(":
            depth += 1
        elif tok.group() == ")":
            depth -= 1
            if depth < 0:
                return False                             # se cerró el paréntesis que contenía la sentencia
        elif depth == 0:
            return True
    return False
