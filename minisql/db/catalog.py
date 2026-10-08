"""Consultas al diccionario de datos sobre esquemas y objetos.

Todas reciben un cursor, corren en un hilo de trabajo y usan bind variables (nunca se concatenan
nombres en el SQL).
"""
import oracledb

# ---------------------------------------------------------------- tipos de objeto

OBJECT_KINDS = {
    "TABLE": "tabla", "VIEW": "vista", "MATERIALIZED VIEW": "vista mat.", "SYNONYM": "sinónimo",
    "PACKAGE": "paquete", "PROCEDURE": "procedimiento", "FUNCTION": "función",
    "SEQUENCE": "secuencia", "TYPE": "tipo",
}
WITH_COLUMNS = {"TABLE", "VIEW", "MATERIALIZED VIEW"}
WITH_DATA = WITH_COLUMNS | {"SYNONYM"}
SOURCE_TYPES = {"PROCEDURE", "FUNCTION", "PACKAGE", "TRIGGER", "TYPE"}
OPENABLE_TYPES = ("TABLE", "VIEW", "MATERIALIZED VIEW", "PACKAGE", "PROCEDURE", "FUNCTION",
                  "TYPE", "TRIGGER", "SEQUENCE", "SYNONYM", "INDEX")


def kind_label(otype):
    """'TABLE' -> 'tabla'."""
    return OBJECT_KINDS.get(otype, otype.lower())


def format_type(dtype, length, precision, scale, char_length):
    """Tipo de columna como se escribe: VARCHAR2(50), NUMBER(10,2)…"""
    if dtype in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR"):
        return f"{dtype}({char_length or length})"
    if dtype == "NUMBER" and precision is not None:
        return f"NUMBER({precision},{scale})" if scale else f"NUMBER({precision})"
    if dtype == "RAW":
        return f"RAW({length})"
    return dtype


# ---------------------------------------------------------------- explorador

def list_schemas(cur, hide_system=False):
    """Usuarios de la base. Con hide_system, sin los esquemas propios de Oracle (12c o superior)."""
    if hide_system:
        try:
            cur.execute("select username from all_users where oracle_maintained = 'N' order by username")
            return [r[0] for r in cur.fetchall()]
        except oracledb.DatabaseError:
            pass  # Oracle anterior a 12c: no existe ORACLE_MAINTAINED
    cur.execute("select username from all_users order by username")
    return [r[0] for r in cur.fetchall()]


def list_objects(cur, owner, otype):
    """[(nombre, estado)] de los objetos de un tipo. Las tablas no incluyen las de vistas materializadas."""
    if otype == "TABLE":
        cur.execute("""
            select o.object_name, o.status from all_objects o
            where o.owner = :own and o.object_type = 'TABLE' and o.object_name not like 'BIN$%'
              and not exists (select 1 from all_mviews m
                              where m.owner = o.owner and m.mview_name = o.object_name)
            order by o.object_name""", own=owner)
    else:
        cur.execute("""
            select object_name, status from all_objects
            where owner = :own and object_type = :typ and object_name not like 'BIN$%'
            order by object_name""", own=owner, typ=otype)
    return cur.fetchall()


def list_columns(cur, owner, name):
    """[(columna, tipo, largo, precisión, escala, largo en caracteres, nulo, 'P' si es PK)]."""
    cur.execute("""
        select c.column_name, c.data_type, c.data_length, c.data_precision, c.data_scale,
               c.char_length, c.nullable,
               (select 'P' from all_cons_columns cc
                  join all_constraints k on k.owner = cc.owner and k.constraint_name = cc.constraint_name
                 where k.constraint_type = 'P' and cc.owner = c.owner
                   and cc.table_name = c.table_name and cc.column_name = c.column_name
                   and rownum = 1) as pk
        from all_tab_columns c
        where c.owner = :own and c.table_name = :tab
        order by c.column_id""", own=owner, tab=name)
    return cur.fetchall()


def list_subprograms(cur, owner, package):
    """Procedimientos y funciones de un paquete, en el orden en que están declarados."""
    cur.execute("""
        select procedure_name, min(subprogram_id) from all_procedures
        where owner = :own and object_name = :pkg and procedure_name is not null
        group by procedure_name order by 2""", own=owner, pkg=package)
    return [r[0] for r in cur.fetchall()]


# ---------------------------------------------------------------- autocompletado y F4

def completion_objects(cur, owner):
    """{nombre: tipo} de lo que se puede escribir en una sentencia (tablas, vistas, paquetes…)."""
    cur.execute("""
        select object_name, object_type from all_objects
        where owner = :own and object_name not like 'BIN$%'
          and object_type in ('TABLE', 'VIEW', 'MATERIALIZED VIEW', 'SYNONYM', 'PACKAGE',
                              'PROCEDURE', 'FUNCTION', 'SEQUENCE', 'TYPE')""", own=owner)
    found = {}
    for name, otype in cur.fetchall():
        if name not in found or otype == "MATERIALIZED VIEW":   # la vista materializada gana a su tabla
            found[name] = otype
    return found


def describe_object(cur, me, owner, name, depth=0):
    """Qué se puede escribir después de 'OBJETO.': columnas, subprogramas o NEXTVAL/CURRVAL.
    Sigue sinónimos privados y públicos."""
    own = owner or me
    cur.execute("""
        select object_type from all_objects
        where owner = :own and object_name = :n
          and object_type in ('TABLE', 'VIEW', 'MATERIALIZED VIEW', 'PACKAGE', 'SEQUENCE', 'SYNONYM')""",
                own=own, n=name)
    types = {r[0] for r in cur.fetchall()}
    if not types and owner is None:
        own, types = "PUBLIC", {"SYNONYM"}
    if types & WITH_COLUMNS:
        cur.execute("""
            select column_name, data_type, data_length, data_precision, data_scale, char_length
            from all_tab_columns where owner = :own and table_name = :n order by column_id""",
                    own=own, n=name)
        return [(c, format_type(t, ln, p, s, cl)) for c, t, ln, p, s, cl in cur.fetchall()]
    if "PACKAGE" in types:
        cur.execute("""
            select procedure_name, min(subprogram_id) from all_procedures
            where owner = :own and object_name = :n and procedure_name is not null
            group by procedure_name order by 2""", own=own, n=name)
        return [(r[0], "subprograma") for r in cur.fetchall()]
    if "SEQUENCE" in types:
        return [("NEXTVAL", "secuencia"), ("CURRVAL", "secuencia")]
    if "SYNONYM" in types and depth < 3:
        target = _synonym_target(cur, own, name)
        if target:
            return describe_object(cur, me, *target, depth + 1)
    return []


def resolve_object(cur, me, owner, name, depth=0):
    """Busca un objeto por nombre (para abrirlo con F4). Prefiere el del usuario, luego sinónimos
    públicos; un sinónimo se abre como el objeto al que apunta. Devuelve (dueño, nombre, tipo) o None."""
    own = owner or me
    cur.execute("""
        select object_type from all_objects
        where owner = :own and object_name = :nam and object_type in
          ('TABLE', 'VIEW', 'MATERIALIZED VIEW', 'PACKAGE', 'PROCEDURE', 'FUNCTION',
           'TYPE', 'TRIGGER', 'SEQUENCE', 'SYNONYM', 'INDEX')""", own=own, nam=name)
    types = {r[0] for r in cur.fetchall()}
    if not types and owner is None:
        own, types = "PUBLIC", {"SYNONYM"}
    if types - {"SYNONYM"}:
        for t in OPENABLE_TYPES:             # la vista materializada gana a su tabla
            if t in types and not (t == "TABLE" and "MATERIALIZED VIEW" in types):
                return own, name, t
    if "SYNONYM" in types and depth < 3:
        target = _synonym_target(cur, own, name)
        if target:
            return resolve_object(cur, me, *target, depth + 1) or (own, name, "SYNONYM")
        if own != "PUBLIC":
            return own, name, "SYNONYM"
    return None


def _synonym_target(cur, owner, name):
    """(dueño, nombre) al que apunta un sinónimo, o None."""
    cur.execute("select table_owner, table_name from all_synonyms where owner = :own and synonym_name = :nam",
                own=owner, nam=name)
    row = cur.fetchone()
    return (row[0], row[1]) if row and row[0] else None


# ---------------------------------------------------------------- depuración

def fetch_arguments(cur, owner, name, subprogram=None):
    """Argumentos del primer subprograma (o del indicado) para armar la llamada de prueba.
    Devuelve (subprograma, [(argumento, posición, modo, data_type, type_owner, type_name, type_subname)])
    o None si no hay argumentos registrados."""
    cur.execute("""
        select object_name, overload, argument_name, position, in_out, data_type,
               type_owner, type_name, type_subname
        from all_arguments
        where owner = :own and data_level = 0
          and ((package_name = :nam and (:sub is null or object_name = :sub))
               or (package_name is null and object_name = :nam))
        order by object_name, overload nulls first, sequence""", own=owner, nam=name, sub=subprogram)
    rows = cur.fetchall()
    if not rows:
        return None
    first, overload = rows[0][0], rows[0][1]
    return first, [r[2:] for r in rows if r[0] == first and r[1] == overload]
