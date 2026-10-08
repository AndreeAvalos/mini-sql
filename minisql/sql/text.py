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


def word_at(line: str, col: int) -> str:
    """El identificador que contiene la columna col (0 = primer carácter) o ''."""
    for m in re.finditer(r"[A-Za-z_][\w$#]*", line):
        if m.start() <= col < m.end():
            return m.group()
    return ""
