"""Proveedor de sugerencias del autocompletado, con caché de metadatos por conexión."""
from PySide6.QtCore import QObject, Signal

from ..db.catalog import completion_objects, describe_object, kind_label, list_schemas
from ..sql.completion import alias_spelling, completion_target, rank_completions, table_refs
from ..sql.text import SQL_FUNCTIONS, SQL_KEYWORDS

# Fijas: se ordenan una sola vez y no en cada tecla
FUNCTION_ITEMS = [(f, "función", False, 4) for f in sorted(SQL_FUNCTIONS)]
KEYWORD_ITEMS = [(k, "palabra clave", False, 5) for k in sorted(SQL_KEYWORDS)]


class CompletionProvider(QObject):
    """Sugerencias para el editor. Lo que no está en caché se pide con `query` (que corre en un
    hilo) y al llegar se emite `updated` para que el editor vuelva a sugerir.

    query(fn, on_ok, on_err): ejecuta fn(cursor) en la sesión de metadatos.
    schema: esquema del usuario conectado."""
    updated = Signal()

    def __init__(self, query, schema, parent=None):
        super().__init__(parent)
        self.query = query
        self.me = schema
        self.gen = 0
        self.clear()

    def clear(self):
        """Olvida los metadatos (tras un DDL o al refrescar el explorador)."""
        self.gen += 1
        self.schemas = None
        self.objects = {}       # dueño -> [(nombre, tipo)] ordenado
        self.described = {}     # (dueño o "", nombre) -> [(nombre, detalle)]
        self.loading = set()

    @property
    def busy(self):
        return bool(self.loading)

    def _load(self, key, fn, store):
        if key in self.loading:
            return
        self.loading.add(key)
        gen = self.gen

        def ok(result):
            if gen == self.gen:          # si se limpió la caché mientras cargaba, se descarta
                self.loading.discard(key)
                store(result)
                self.updated.emit()

        self.query(fn, ok, lambda _msg: ok(None))

    # --- caché
    def schema_list(self):
        if self.schemas is None:
            self._load("schemas", list_schemas, lambda r: setattr(self, "schemas", r or []))
            return []
        return self.schemas

    def objects_of(self, owner):
        """[(nombre, tipo)] ordenados por nombre."""
        if owner not in self.objects:
            self._load(("objects", owner), lambda cur: completion_objects(cur, owner),
                       lambda r: self.objects.__setitem__(owner, sorted((r or {}).items())))
            return []
        return self.objects[owner]

    def describe(self, owner, name):
        key = (owner or "", name)
        if key not in self.described:
            self._load(("describe", key), lambda cur: describe_object(cur, self.me, owner, name),
                       lambda r: self.described.__setitem__(key, r or []))
            return []
        return self.described[key]

    # --- sugerencias
    def suggest(self, statement, before):
        """Sugerencias para el cursor. Devuelve (prefijo, [(nombre, detalle, es_identificador, alias)]).
        Las columnas sin calificar llevan el alias de su tabla: en 'where fec' se sugiere e.FECHA."""
        quals, prefix = completion_target(before)
        refs = table_refs(statement)
        if not quals:
            items = self._unqualified(statement, refs)
        elif len(quals) == 1 and quals[0] in refs:
            items = [(c, d, True, 0) for c, d in self.describe(*refs[quals[0]])]
        elif len(quals) == 1 and quals[0] in self.schema_list():
            items = [(n, kind_label(t), True, 0) for n, t in self.objects_of(quals[0])]
        else:
            owner = quals[0] if len(quals) == 2 else None
            items = [(c, d, True, 0) for c, d in self.describe(owner, quals[-1])]
        return prefix, rank_completions(items, prefix)

    def _unqualified(self, statement, refs):
        """Sin 'algo.' antes: alias, columnas de las tablas de la sentencia, objetos, esquemas,
        funciones y palabras clave (en ese orden de prioridad)."""
        items = []
        with_alias = set()
        for alias, ref in refs.items():
            if alias != ref[1]:
                items.append((alias, f"alias de {ref[1]}", True, 0))
                qual = alias_spelling(statement, alias)
                items += [(c, f"{d} · {ref[1]}", True, 1, qual) for c, d in self.describe(*ref)]
                with_alias.add(ref)
        for ref in dict.fromkeys(refs.values()):
            if ref not in with_alias:
                items += [(c, f"{d} · {ref[1]}", True, 1) for c, d in self.describe(*ref)]
        items += [(n, kind_label(t), True, 2) for n, t in self.objects_of(self.me)]
        items += [(s, "esquema", True, 3) for s in self.schema_list()]
        return items + FUNCTION_ITEMS + KEYWORD_ITEMS
