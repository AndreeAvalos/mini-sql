"""Código de los objetos: obtener el fuente o el DDL, compilar y leer errores de compilación.
Todas reciben un cursor y corren en un hilo de trabajo."""
import oracledb

from ..sql.plsql import split_units, unit_info
from ..sql.text import q
from .catalog import SOURCE_TYPES


def fetch_source(cur, owner, name):
    """Código PL/SQL desde ALL_SOURCE (especificación y cuerpo), listo para compilar. None si no hay."""
    cur.execute("""
        select type, text from all_source where owner = :own and name = :nam
        order by decode(type, 'PACKAGE BODY', 2, 'TYPE BODY', 2, 1), line""", own=owner, nam=name)
    parts, current = [], None
    for typ, text in cur.fetchall():
        if typ != current:
            if current is not None:
                parts[-1] = parts[-1].rstrip("\n")
                parts.append("\n/\n\n")
            parts.append("CREATE OR REPLACE ")
            current = typ
        parts.append(text)
    return "".join(parts).rstrip() + "\n/" if parts else None


def fetch_ddl(cur, owner, name, otype):
    """DDL del objeto con DBMS_METADATA; si no hay privilegios, el código o el texto de la vista."""
    try:
        cur.execute("select dbms_metadata.get_ddl(:typ, :nam, :own) from dual",
                    typ=otype.replace(" ", "_"), nam=name, own=owner)
        return cur.fetchone()[0].strip()
    except oracledb.DatabaseError:
        if otype in SOURCE_TYPES:
            src = fetch_source(cur, owner, name)
            if src:
                return src
        if otype == "VIEW":
            cur.execute("select text from all_views where owner = :own and view_name = :nam",
                        own=owner, nam=name)
            r = cur.fetchone()
            if r:
                return f"CREATE OR REPLACE VIEW {q(owner)}.{q(name)} AS\n{r[0]}"
        raise


def fetch_errors(cur, owner, name):
    """[(tipo de unidad, línea, columna, texto)] de ALL_ERRORS."""
    cur.execute("""
        select type, line, position, text from all_errors
        where owner = :own and name = :nam order by sequence""", own=owner, nam=name)
    return cur.fetchall()


def compile_code(cur, code, owner, me):
    """Compila cada unidad del código y devuelve los errores de ALL_ERRORS.
    Usa CURRENT_SCHEMA = dueño para que todo se cree y resuelva en el esquema del objeto."""
    units = split_units(code)
    if not units:
        raise ValueError("No hay código para compilar")
    infos = []
    for start, text in units:
        info = unit_info(text, owner)
        if not info:
            raise ValueError(f"La unidad que empieza en la línea {start} no es "
                             "CREATE OR REPLACE PROCEDURE/FUNCTION/PACKAGE/TYPE/TRIGGER")
        infos.append(info)
    cur.execute(f"alter session set current_schema = {q(owner)}")
    try:
        for _start, text in units:
            try:
                cur.execute(text)
            except oracledb.DatabaseError as e:
                if "24344" not in str(e):      # "compilado con errores": se leen de ALL_ERRORS
                    raise
    finally:
        cur.execute(f"alter session set current_schema = {q(me)}")
    errors = []
    for own, name in dict.fromkeys((o, n) for _kind, o, n in infos):
        errors += fetch_errors(cur, own, name)
    return errors
