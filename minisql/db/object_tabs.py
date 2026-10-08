"""Contenido de las pestañas del visor de objetos.

Cada pestaña tiene su cargador registrado con @tab_loader("Nombre"). Para agregar una pestaña nueva
basta con escribir su cargador aquí y nombrarla en OBJECT_TABS (ui/object_viewer.py); no hay que
tocar el resto del código. Los cargadores reciben un cursor, corren en un hilo de trabajo y
devuelven Grid(...) o Text(...).
"""
from typing import NamedTuple

from .catalog import format_type
from .source import fetch_ddl, fetch_errors, fetch_source


class Grid(NamedTuple):
    headers: list
    rows: list


class Text(NamedTuple):
    text: str


TAB_LOADERS = {}


def tab_loader(label):
    def register(fn):
        TAB_LOADERS[label] = fn
        return fn
    return register


def fetch_object_tab(cur, owner, name, otype, tab):
    """Carga la pestaña `tab` del objeto. Devuelve Grid o Text."""
    try:
        loader = TAB_LOADERS[tab]
    except KeyError:
        raise ValueError(f"Pestaña desconocida: {tab}") from None
    return loader(cur, owner, name, otype)


# Datos propios de cada tipo para la pestaña "Detalles" (además de ALL_OBJECTS)
DETAIL_QUERIES = {
    "TABLE": """select t.tablespace_name, t.num_rows, t.blocks, t.avg_row_len, t.last_analyzed,
                       t.partitioned, t.temporary, t.logging, c.comments
                from all_tables t left join all_tab_comments c on c.owner = t.owner and c.table_name = t.table_name
                where t.owner = :own and t.table_name = :nam""",
    "VIEW": """select c.comments from all_tab_comments c where c.owner = :own and c.table_name = :nam""",
    "MATERIALIZED VIEW": """select refresh_mode, refresh_method, last_refresh_date, staleness, compile_state
                            from all_mviews where owner = :own and mview_name = :nam""",
    "SEQUENCE": """select min_value, max_value, increment_by, cycle_flag, order_flag, cache_size, last_number
                   from all_sequences where sequence_owner = :own and sequence_name = :nam""",
    "SYNONYM": """select table_owner, table_name, db_link from all_synonyms
                  where owner = :own and synonym_name = :nam""",
    "INDEX": """select table_owner, table_name, index_type, uniqueness, status, tablespace_name,
                       num_rows, last_analyzed
                from all_indexes where owner = :own and index_name = :nam""",
    "TRIGGER": """select table_owner, table_name, base_object_type, trigger_type, triggering_event,
                         status, when_clause
                  from all_triggers where owner = :own and trigger_name = :nam""",
}


@tab_loader("Columnas")
def load_columns(cur, owner, name, otype):
    if otype == "INDEX":
        cur.execute("""
            select ic.column_position, ic.column_name, ic.descend
            from all_ind_columns ic where ic.index_owner = :own and ic.index_name = :nam
            order by ic.column_position""", own=owner, nam=name)
        return Grid(["#", "Columna", "Orden"], cur.fetchall())
    cur.execute("""
        select cc.column_name from all_cons_columns cc
        join all_constraints k on k.owner = cc.owner and k.constraint_name = cc.constraint_name
        where k.constraint_type = 'P' and k.owner = :own and k.table_name = :nam""", own=owner, nam=name)
    pk = {r[0] for r in cur.fetchall()}
    cur.execute("""
        select c.column_id, c.column_name, c.data_type, c.data_length, c.data_precision, c.data_scale,
               c.char_length, c.nullable, c.data_default, m.comments
        from all_tab_columns c
        left join all_col_comments m
          on m.owner = c.owner and m.table_name = c.table_name and m.column_name = c.column_name
        where c.owner = :own and c.table_name = :nam
        order by c.column_id""", own=owner, nam=name)
    rows = [(cid, col, format_type(t, ln, p, s, cl), "Sí" if nullable == "Y" else "No",
             (default or "").strip() or None, "🔑" if col in pk else "", comments)
            for cid, col, t, ln, p, s, cl, nullable, default, comments in cur.fetchall()]
    return Grid(["#", "Columna", "Tipo", "Nulo", "Default", "PK", "Comentario"], rows)


@tab_loader("Índices")
def load_indexes(cur, owner, name, _otype):
    cur.execute("""
        select i.index_name, i.uniqueness, i.index_type, i.status,
               listagg(ic.column_name, ', ') within group (order by ic.column_position)
        from all_indexes i
        join all_ind_columns ic on ic.index_owner = i.owner and ic.index_name = i.index_name
        where i.table_owner = :own and i.table_name = :nam
        group by i.index_name, i.uniqueness, i.index_type, i.status
        order by i.index_name""", own=owner, nam=name)
    return Grid(["Índice", "Unicidad", "Tipo", "Estado", "Columnas"], cur.fetchall())


@tab_loader("Restricciones")
def load_constraints(cur, owner, name, _otype):
    cur.execute("""
        select c.constraint_name,
               decode(c.constraint_type, 'P', 'Primary key', 'U', 'Unique', 'R', 'Foreign key',
                      'C', 'Check', c.constraint_type),
               (select listagg(cc.column_name, ', ') within group (order by cc.position)
                  from all_cons_columns cc
                 where cc.owner = c.owner and cc.constraint_name = c.constraint_name),
               (select r.table_name from all_constraints r
                 where r.owner = c.r_owner and r.constraint_name = c.r_constraint_name),
               c.search_condition, c.status
        from all_constraints c where c.owner = :own and c.table_name = :nam
        order by decode(c.constraint_type, 'P', 1, 'U', 2, 'R', 3, 4), c.constraint_name""",
                own=owner, nam=name)
    return Grid(["Restricción", "Tipo", "Columnas", "Referencia a", "Condición", "Estado"], cur.fetchall())


@tab_loader("Argumentos")
def load_arguments(cur, owner, name, _otype):
    cur.execute("""
        select object_name, overload, position, nvl(argument_name, '(retorno)'), in_out, data_type
        from all_arguments
        where owner = :own and data_level = 0
          and ((package_name = :nam) or (package_name is null and object_name = :nam))
        order by object_name, overload, sequence""", own=owner, nam=name)
    return Grid(["Subprograma", "Sobrecarga", "Posición", "Argumento", "Modo", "Tipo"], cur.fetchall())


@tab_loader("Errores")
def load_errors(cur, owner, name, _otype):
    return Grid(["Parte", "Línea", "Columna", "Error"], fetch_errors(cur, owner, name))


@tab_loader("Detalles")
def load_details(cur, owner, name, otype):
    cur.execute("""
        select object_type, status, created, last_ddl_time from all_objects
        where owner = :own and object_name = :nam and object_type = :typ""",
                own=owner, nam=name, typ=otype)
    row = cur.fetchone() or (otype, None, None, None)
    props = [("Esquema", owner), ("Nombre", name), ("Tipo", row[0]), ("Estado", row[1]),
             ("Creado", row[2]), ("Último DDL", row[3])]
    if otype in DETAIL_QUERIES:
        cur.execute(DETAIL_QUERIES[otype], own=owner, nam=name)
        extra = cur.fetchone()
        if extra:
            props += [(d[0].replace("_", " ").capitalize(), v) for d, v in zip(cur.description, extra, strict=False)]
    return Grid(["Propiedad", "Valor"], props)


@tab_loader("Código")
def load_code(cur, owner, name, otype):
    return Text(fetch_source(cur, owner, name) or fetch_ddl(cur, owner, name, otype))


@tab_loader("DDL")
def load_ddl(cur, owner, name, otype):
    return Text(fetch_ddl(cur, owner, name, otype))
