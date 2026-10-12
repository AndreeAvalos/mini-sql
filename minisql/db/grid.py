"""Edición de resultados: qué admite cada columna, convertir lo que se escribe y guardar por ROWID.
Sin Qt; las funciones con cursor corren en un hilo de trabajo."""
import datetime
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import oracledb

from ..sql.text import q

TEXT_TYPES = {"VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR", "VARCHAR"}
NUMBER_TYPES = {"NUMBER", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE", "INTEGER"}
LOB_TEXT_TYPES = {"CLOB", "NCLOB"}
DATE_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y")


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    length: int | None = None          # bytes (DATA_LENGTH)
    precision: int | None = None
    scale: int | None = None
    char_length: int | None = None     # caracteres (CHAR_LENGTH)
    char_used: str | None = None       # 'C' = largo en caracteres, 'B' = en bytes
    nullable: bool = True
    has_default: bool = False
    readonly_reason: str | None = None  # por qué no se puede editar (virtual, identidad, BLOB…)

    @property
    def base_type(self):
        return re.sub(r"\(.*", "", self.data_type).strip()

    def describe(self):
        """Tipo como se escribe en Oracle, para mensajes: VARCHAR2(50 CHAR), NUMBER(10,2)…"""
        t = self.base_type
        if t in TEXT_TYPES:
            unit = " CHAR" if self.char_used == "C" and t in ("VARCHAR2", "CHAR") else ""
            return f"{t}({self.char_length or self.length}{unit})"
        if t == "NUMBER" and self.precision is not None:
            return f"NUMBER({self.precision},{self.scale})" if self.scale else f"NUMBER({self.precision})"
        if t == "RAW":
            return f"RAW({self.length})"
        return self.data_type


def _grid_editable_type(base):
    """Tipos que se pueden escribir en una celda: texto, números, fechas y RAW (en hexadecimal).
    BLOB, LONG, ROWID, INTERVAL, XMLTYPE y tipos de objeto quedan de solo lectura."""
    return (base in TEXT_TYPES | NUMBER_TYPES | LOB_TEXT_TYPES or base in ("DATE", "RAW")
            or base.startswith("TIMESTAMP"))


def fetch_columns_info(cur, owner, table):
    """{COLUMNA: ColumnInfo} de una tabla, con lo que se puede validar antes de guardar."""
    sql = """
        select column_name, data_type, data_length, data_precision, data_scale, char_length, char_used,
               nullable, virtual_column, {identity}, data_default
        from all_tab_cols
        where owner = :own and table_name = :nam and hidden_column = 'NO'
        order by column_id"""
    try:
        cur.execute(sql.format(identity="identity_column"), own=owner, nam=table)
    except oracledb.DatabaseError:            # Oracle 11g: no existe IDENTITY_COLUMN
        cur.execute(sql.format(identity="'NO'"), own=owner, nam=table)
    columns = {}
    for name, dtype, length, prec, scale, clen, cused, nullable, virtual, identity, default in cur.fetchall():
        info = ColumnInfo(name, dtype, length, prec, scale, clen, cused, nullable == "Y",
                          bool(default and str(default).strip()))
        base = info.base_type
        if virtual == "YES":
            info.readonly_reason = "es una columna virtual (calculada)"
        elif identity == "YES":
            info.readonly_reason = "es una columna de identidad (la llena Oracle)"
        elif not _grid_editable_type(base):
            info.readonly_reason = f"el tipo {base} no se edita en la grilla"
        columns[name] = info
    return columns


def parse_value(text, col):
    """Convierte lo escrito en la celda al valor para Oracle, o lanza ValueError con el motivo.
    Texto vacío es NULL (como en Oracle, donde '' es NULL)."""
    if col.readonly_reason:
        raise ValueError(f"{col.name} no se puede editar: {col.readonly_reason}")
    raw = "" if text is None else str(text)
    blank = raw == "" or (raw.strip() == "" and col.base_type not in TEXT_TYPES | LOB_TEXT_TYPES)
    if blank:                                   # en un texto, "   " es un valor; en un número o fecha, nada
        if not col.nullable:
            raise ValueError(f"{col.name} es obligatoria (NOT NULL): no puede quedar vacía")
        return None
    base = col.base_type
    if base in TEXT_TYPES:
        limit = col.char_length or col.length
        size = len(raw) if col.char_used == "C" or base.startswith("N") else len(raw.encode("utf-8"))
        unit = "caracteres" if col.char_used == "C" or base.startswith("N") else "bytes"
        if limit and size > limit:
            raise ValueError(f"{col.name} admite hasta {limit} {unit} ({col.describe()}); escribiste {size}")
        return raw
    if base in LOB_TEXT_TYPES:
        return raw
    if base in NUMBER_TYPES:
        return _parse_number(raw.strip(), col)
    if base == "DATE" or base.startswith("TIMESTAMP"):
        return _parse_date(raw.strip(), col)
    if base == "RAW":
        try:
            value = bytes.fromhex(raw.strip())
        except ValueError:
            raise ValueError(f"{col.name} es RAW: escribe el valor en hexadecimal (p. ej. 0A1B2C)") from None
        if col.length and len(value) > col.length:
            raise ValueError(f"{col.name} admite hasta {col.length} bytes ({col.describe()})")
        return value
    raise ValueError(f"{col.name} no se puede editar en la grilla")


def _parse_number(text, col):
    normalized = text.replace(" ", "")
    if normalized.count(",") == 1 and "." not in normalized:
        normalized = normalized.replace(",", ".")          # 12,5 -> 12.5
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        raise ValueError(f"{col.name} es numérica ({col.describe()}): «{text}» no es un número") from None
    if not value.is_finite():
        raise ValueError(f"{col.name}: «{text}» no es un número válido")
    if col.base_type == "NUMBER" and col.precision is not None:
        scale = col.scale or 0
        _sign, digits, exponent = value.normalize().as_tuple()
        decimals = max(0, -exponent)
        integers = max(0, len(digits) + exponent)
        if decimals > scale:
            what = "no admite decimales" if scale == 0 else f"admite hasta {scale} decimales"
            raise ValueError(f"{col.name} {what} ({col.describe()})")
        if integers > col.precision - scale:
            raise ValueError(f"{col.name} admite hasta {col.precision - scale} dígitos enteros ({col.describe()})")
    if col.scale == 0 and value == value.to_integral_value():
        return int(value)
    return value


def _parse_date(text, col):
    for fmt in DATE_FORMATS:
        try:
            value = datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
        if col.base_type == "DATE" and value.microsecond:
            raise ValueError(f"{col.name} es DATE: no guarda fracciones de segundo")
        return value
    raise ValueError(f"{col.name} es {col.base_type}: escribe la fecha como 2026-10-12, "
                     "2026-10-12 14:30 o 12/10/2026")


def apply_changes(cur, owner, table, ops):
    """Aplica los cambios de la grilla, cada uno por ROWID y con bind variables:
        ("update", rowid, {columna: valor}) · ("insert", {columna: valor}) · ("delete", rowid)
    Si uno falla o no encuentra su fila, se deshacen todos (savepoint) y se lanza el error.
    Devuelve el ROWID de cada fila insertada, en orden. No hace COMMIT."""
    target = f"{q(owner)}.{q(table)}"
    cur.execute("savepoint minisql_grilla")
    new_rowids = []
    try:
        for op in ops:
            kind = op[0]
            if kind == "update":
                _, rowid, values = op
                sets = ", ".join(f"{q(c)} = :v{i}" for i, c in enumerate(values))
                binds = {f"v{i}": v for i, v in enumerate(values.values())}
                cur.execute(f"update {target} set {sets} where rowid = chartorowid(:rid)", rid=rowid, **binds)
                _expect_one(cur, "actualizar", rowid)
            elif kind == "delete":
                _, rowid = op
                cur.execute(f"delete from {target} where rowid = chartorowid(:rid)", rid=rowid)
                _expect_one(cur, "borrar", rowid)
            elif kind == "insert":
                _, values = op
                if not values:
                    raise ValueError("Hay una fila nueva sin ningún valor: llénala o bórrala antes de guardar")
                out = cur.var(str, 40)
                cols = ", ".join(q(c) for c in values)
                marks = ", ".join(f":v{i}" for i in range(len(values)))
                binds = {f"v{i}": v for i, v in enumerate(values.values())}
                cur.execute(f"insert into {target} ({cols}) values ({marks}) "
                            "returning rowidtochar(rowid) into :rid", rid=out, **binds)
                value = out.getvalue()
                new_rowids.append(value[0] if isinstance(value, list) else value)
            else:
                raise ValueError(f"Operación desconocida: {kind}")
    except Exception:
        cur.execute("rollback to savepoint minisql_grilla")
        raise
    return new_rowids


def sql_literal(value):
    """Un valor escrito como literal de Oracle, solo para mostrarlo (al guardar se usan bind variables)."""
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, (bytes, bytearray)):
        return f"HEXTORAW('{bytes(value).hex().upper()}')"
    if isinstance(value, datetime.datetime):
        if value.microsecond:
            return f"TO_TIMESTAMP('{value:%Y-%m-%d %H:%M:%S.%f}', 'YYYY-MM-DD HH24:MI:SS.FF6')"
        if value.time() == datetime.time():
            return f"TO_DATE('{value:%Y-%m-%d}', 'YYYY-MM-DD')"
        return f"TO_DATE('{value:%Y-%m-%d %H:%M:%S}', 'YYYY-MM-DD HH24:MI:SS')"
    if isinstance(value, datetime.date):
        return f"TO_DATE('{value:%Y-%m-%d}', 'YYYY-MM-DD')"
    return str(value)


def preview_sql(owner, table, ops):
    """Las sentencias que hará apply_changes, con los valores a la vista, para confirmarlas antes de guardar."""
    target = f"{q(owner)}.{q(table)}"
    out = []
    for op in ops:
        if op[0] == "update":
            _, rowid, values = op
            sets = ",\n       ".join(f"{q(c)} = {sql_literal(v)}" for c, v in values.items())
            out.append(f"UPDATE {target}\n   SET {sets}\n WHERE ROWID = '{rowid}';")
        elif op[0] == "delete":
            out.append(f"DELETE FROM {target}\n WHERE ROWID = '{op[1]}';")
        elif op[0] == "insert":
            values = op[1]
            cols = ", ".join(q(c) for c in values)
            vals = ", ".join(sql_literal(v) for v in values.values())
            out.append(f"INSERT INTO {target} ({cols})\nVALUES ({vals});")
    return out


def _expect_one(cur, action, rowid):
    if cur.rowcount != 1:
        raise RuntimeError(f"No se pudo {action} la fila (ROWID {rowid}): ya no existe o fue movida. "
                           "Vuelve a ejecutar la consulta para ver los datos actuales.")
