"""Tabla de resultados: encabezado destacado, un color por tipo de dato, ordenar con clic en el encabezado,
filtrar y (si la consulta es de una sola tabla) editar."""
import datetime
import numbers
import re
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QKeySequence
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from ..config import MAX_CELL_CHARS
from ..db.grid import DATE_FORMATS, parse_value
from .style import RESULT_THEME, font_zoom, result_stylesheet, result_theme, ui_font


def kind_of(value):
    """Tipo de dato para elegir color y alineación: null, number, date, binary o text."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "text"
    if isinstance(value, numbers.Number):              # int, float y Decimal (NUMBER de Oracle)
        return "number"
    if isinstance(value, (datetime.date, datetime.time, datetime.timedelta)):
        return "date"
    if isinstance(value, (bytes, bytearray)):
        return "binary"
    return "text"


RAW_SHOWN_BYTES = 64          # un RAW corto (p. ej. VERSIONS_XID, SYS_GUID) se ve completo en hexadecimal


def hex_text(value, limit=None):
    """Binario en hexadecimal como lo muestra Oracle (RAWTOHEX): 0A001F00A2B30000."""
    data = bytes(value if limit is None else value[:limit])
    text = data.hex().upper()
    return text + "…" if limit is not None and len(value) > limit else text


def display_text(value):
    """Cómo se ve un valor en la celda."""
    if value is None:
        return "(null)"
    if isinstance(value, (bytes, bytearray)):
        if len(value) <= RAW_SHOWN_BYTES:
            return hex_text(value)
        return f"<{len(value)} bytes> {hex_text(value, 16)}"     # BLOB: tamaño y el inicio
    if isinstance(value, datetime.datetime):
        if value.microsecond:
            return value.isoformat(sep=" ")
        if (value.hour, value.minute, value.second) == (0, 0, 0):
            return value.strftime("%Y-%m-%d")             # DATE de Oracle sin hora
        return value.strftime("%Y-%m-%d %H:%M:%S")
    s = str(value)
    return s if len(s) <= MAX_CELL_CHARS else s[:MAX_CELL_CHARS] + "…"


def sort_key(value):
    """Ordena dentro de cada tipo; si una columna mezcla tipos, no falla (los agrupa)."""
    kind = kind_of(value)
    if kind in ("number", "date"):
        return (kind, value)
    return (kind, str(value))


def edit_text(value):
    """El valor como se edita en la celda: completo, sin "(null)" (vacío es NULL) y RAW en hexadecimal."""
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex().upper()
    if isinstance(value, datetime.datetime):
        return display_text(value)
    return str(value)


_OPERATOR = re.compile(r"^(>=|<=|!=|<>|=|>|<)\s*(.*)$")


def _as_number(text):
    try:
        return Decimal(text.replace(" ", "").replace(",", ".") if text.count(",") == 1 and "." not in text
                       else text.replace(" ", ""))
    except InvalidOperation:
        return None


def _as_date(text):
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None


def make_matcher(text):
    """Convierte lo escrito en el filtro en una función valor -> bool, o None si no hay filtro.
        texto       contiene (sin distinguir mayúsculas)
        =x  !=x     igual / distinto
        >x  <x  >=x  <=x   compara números y fechas (2026-01-31); si no, como texto
        null  !null        vacío / con valor"""
    t = (text or "").strip()
    if not t:
        return None
    low = t.lower()
    if low in ("null", "=null", "is null"):
        return lambda v: v is None
    if low in ("!null", "!=null", "<>null", "not null", "is not null"):
        return lambda v: v is not None
    m = _OPERATOR.match(t)
    if not m:
        return lambda v: v is not None and low in display_text(v).lower()
    op, operand = m.group(1), m.group(2).strip()

    def compare(v):
        if v is None:
            return op in ("!=", "<>")
        left, right = None, None
        if kind_of(v) == "number" and _as_number(operand) is not None:
            left, right = Decimal(str(v)), _as_number(operand)
        elif isinstance(v, datetime.date) and _as_date(operand) is not None:
            left = v if isinstance(v, datetime.datetime) else datetime.datetime(v.year, v.month, v.day)
            right = _as_date(operand)
        else:
            left, right = display_text(v).lower(), operand.lower()
        return {"=": left == right, "!=": left != right, "<>": left != right, ">": left > right,
                "<": left < right, ">=": left >= right, "<=": left <= right}[op]
    return compare


class ResultModel(QAbstractTableModel):
    def __init__(self, headers=None, rows=None, theme=None, page_size=None):
        """page_size: filas por página (None = todas en una; así lo usan las tablas chicas del visor)."""
        super().__init__()
        self.headers = headers or []
        self.all_rows = list(self._wrap(rows or []))   # todas las filas cargadas (en el orden actual)
        self.matcher = None
        self.filter_column = None
        self.page_size = page_size
        self.page = 0
        self._refresh()
        self.colors = {k: QColor(v) for k, v in (theme or RESULT_THEME["dark"]).items()}
        self.italic = QFont()
        self.italic.setItalic(True)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        v = self.rows[index.row()][index.column()]
        if role == Qt.DisplayRole:
            return display_text(v)
        kind = kind_of(v)
        if role == Qt.ForegroundRole and kind != "text":
            return self.colors[kind]
        if role == Qt.FontRole and kind == "null":
            return self.italic
        if role == Qt.TextAlignmentRole and kind == "number":
            return Qt.AlignRight | Qt.AlignVCenter
        if role == Qt.ToolTipRole and isinstance(v, str) and len(v) > MAX_CELL_CHARS:
            return v[:4000]                                   # el texto largo completo al pasar el mouse
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return self.headers[section]
            if role == Qt.ToolTipRole:
                return f"{self.headers[section]}\nClic para ordenar"
            return None
        if role == Qt.DisplayRole:
            return self.first_number + section
        if role == Qt.TextAlignmentRole:
            return Qt.AlignRight | Qt.AlignVCenter
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        """Clic en el encabezado. Los nulos quedan siempre al final."""
        if not 0 <= column < len(self.headers):
            return
        self.layoutAboutToBeChanged.emit()
        values = [r for r in self.all_rows if r[column] is not None]
        nulls = [r for r in self.all_rows if r[column] is None]
        values.sort(key=lambda r: sort_key(r[column]), reverse=order == Qt.DescendingOrder)
        self.all_rows = values + nulls
        self._refresh(page=0)
        self.layoutChanged.emit()

    # --- filtro
    def set_filter(self, text, column=None):
        """Muestra solo las filas que cumplen el filtro, en todas las columnas o en `column`."""
        self.beginResetModel()
        self.matcher = make_matcher(text)
        self.filter_column = column
        self._refresh(page=0)
        self.endResetModel()

    # --- páginas (sobre las filas cargadas que pasan el filtro)
    def _refresh(self, page=None):
        """Recalcula lo que se ve: filas que pasan el filtro (filtered) y las de la página actual (rows)."""
        self.filtered = [r for r in self.all_rows if self._visible(r)]
        if page is not None:
            self.page = page
        self.page = max(0, min(self.page, self.page_count() - 1))
        if self.page_size:
            start = self.page * self.page_size
            self.rows = self.filtered[start:start + self.page_size]
        else:
            self.rows = list(self.filtered)

    def page_count(self):
        if not self.page_size:
            return 1
        return max(1, -(-len(self.filtered) // self.page_size))

    @property
    def first_number(self):
        """Número (desde 1) de la primera fila de la página."""
        return 1 + self.page * (self.page_size or 0)

    def set_page(self, page):
        self.beginResetModel()
        self._refresh(page)
        self.endResetModel()

    def append_rows(self, rows, rowids=None):
        """Agrega filas traídas de Oracle (página siguiente) antes de las filas nuevas sin guardar."""
        self.beginResetModel()
        at = next((i for i, r in enumerate(self.all_rows) if getattr(r, "new", False)), len(self.all_rows))
        self.all_rows[at:at] = self._wrap(rows, rowids)
        self._refresh()
        self.endResetModel()

    @staticmethod
    def _wrap(rows, rowids=None):
        return list(rows)

    def _visible(self, row):
        if self.matcher is None:
            return True
        if self.filter_column is not None:
            return self.matcher(row[self.filter_column])
        return any(self.matcher(v) for v in row)


class ResultView(QTableView):
    def __init__(self):
        super().__init__()
        self.theme = result_theme(self)
        self.setStyleSheet(result_stylesheet(self.theme))
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setMinimumHeight(28)
        self.base_font = QFont(self.font())
        self.apply_font_size()
        font_zoom.changed.connect(self.apply_font_size)
        # Al ajustar columnas, medir solo las primeras filas: medir 1000 filas traba la interfaz.
        header.setResizeContentsPrecision(100)
        self.verticalHeader().setResizeContentsPrecision(100)
        self._unsorted()
        self.setSortingEnabled(True)

    def apply_font_size(self, _size=None):
        """Misma letra que la app más el zoom (Ctrl+rueda); la altura de las filas la acompaña."""
        font = ui_font(self.base_font)
        self.setFont(font)
        self.horizontalHeader().setFont(font)
        self.verticalHeader().setFont(font)
        self.verticalHeader().setDefaultSectionSize(max(22, QFontMetrics(font).height() + 8))

    def wheelEvent(self, event):
        if not font_zoom.wheel(event):
            super().wheelEvent(event)

    def _unsorted(self):
        """Sin columna de orden: los resultados quedan en el orden de la consulta (su ORDER BY)."""
        self.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)

    def setModel(self, model):
        self._unsorted()                 # cada resultado nuevo llega en su orden, sin reordenar
        # Qt no se queda con el modelo: si nadie en Python lo retiene (p. ej. setModel(ResultModel(...))), el
        # recolector lo borra mientras la tabla lo usa y PySide6 se cae (segfault en Linux)
        self._model_ref = model
        super().setModel(model)

    def show_rows(self, headers, rows):
        model = ResultModel(headers, rows, self.theme)
        self.setModel(model)
        self.fit_columns()
        return model

    def fit_columns(self, max_width=400):
        self.resizeColumnsToContents()
        for c in range(self.model().columnCount()):
            if self.columnWidth(c) > max_width:
                self.setColumnWidth(c, max_width)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy):
            self.copy_selection()
            return
        super().keyPressEvent(event)

    def copy_selection(self):
        idxs = self.selectedIndexes()
        model = self.model()
        if not idxs or not isinstance(model, ResultModel):
            return
        rows = sorted({i.row() for i in idxs})
        cols = sorted({i.column() for i in idxs})
        lines = []
        for r in rows:
            vals = []
            for c in cols:
                v = model.rows[r][c]
                if v is None:
                    vals.append("")
                elif isinstance(v, (bytes, bytearray)):
                    vals.append(hex_text(v, 1_000_000))            # RAW/BLOB en hexadecimal, para HEXTORAW('…')
                else:
                    vals.append(str(v).replace("\t", " ").replace("\n", " "))
            lines.append("\t".join(vals))
        QGuiApplication.clipboard().setText("\n".join(lines))


# ---------------------------------------------------------------- edición

class GridRow(list):
    """Una fila de la grilla editable: sus valores, su ROWID y cómo era al leerla (None si es nueva)."""

    def __init__(self, values, rowid=None, original=None):
        super().__init__(values)
        self.rowid = rowid
        self.original = original          # tupla de valores leídos de Oracle; None = fila nueva
        self.deleted = False

    @property
    def new(self):
        return self.original is None

    def changed_columns(self):
        if self.new:
            return {i for i, v in enumerate(self) if v is not None}
        return {i for i, (now, before) in enumerate(zip(self, self.original, strict=True))
                if (now is None) != (before is None) or edit_text(now) != edit_text(before)}


class EditableResultModel(ResultModel):
    """Resultados de una sola tabla que se pueden editar. Valida cada valor contra la definición de la
    columna al escribirlo; los cambios quedan aquí hasta que se guardan (pending_ops) o se cancelan."""
    changed = Signal()          # cambió algo pendiente de guardar (para habilitar Guardar/Cancelar)
    rejected = Signal(str)      # un valor no se aceptó: el motivo

    def __init__(self, headers, rows, rowids, theme=None, page_size=None):
        self._first_rowids = rowids
        super().__init__(headers, rows, theme, page_size)
        self.columns = [None] * len(self.headers)    # ColumnInfo de cada columna editable (None = no)
        self.strike = QFont()
        self.strike.setStrikeOut(True)
        self.edit_colors = {k: self.colors[k] for k in ("modified", "new", "deleted")}

    def _wrap(self, rows, rowids=None):
        if rowids is None:
            rowids, self._first_rowids = self._first_rowids, None
        return [GridRow(r, rid, tuple(r)) for r, rid in zip(rows, rowids, strict=True)]

    def set_columns(self, columns):
        """columns: ColumnInfo (o None si no se puede editar) para cada columna del resultado."""
        self.columns = list(columns)
        self.headerDataChanged.emit(Qt.Horizontal, 0, max(0, len(self.headers) - 1))

    @property
    def editable(self):
        return any(c is not None and not c.readonly_reason for c in self.columns)

    def _editable_column(self, column):
        info = self.columns[column] if column < len(self.columns) else None
        return info if info is not None and not info.readonly_reason else None

    # --- Qt
    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and self._editable_column(index.column()) and not self.rows[index.row()].deleted:
            flags |= Qt.ItemIsEditable
        return flags

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == Qt.EditRole:
            return edit_text(row[index.column()])
        if role == Qt.BackgroundRole:
            if row.deleted:
                return self.edit_colors["deleted"]
            if row.new:
                return self.edit_colors["new"]
            if index.column() in row.changed_columns():
                return self.edit_colors["modified"]
            return None
        if role == Qt.FontRole and row.deleted:
            return self.strike
        return super().data(index, role)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.ToolTipRole and section < len(self.columns):
            info = self.columns[section]
            if info is None:
                return f"{self.headers[section]}\nSolo lectura: no es una columna de la tabla\nClic para ordenar"
            detail = info.describe() + ("" if info.nullable else " · obligatoria (NOT NULL)")
            state = f"Solo lectura: {info.readonly_reason}" if info.readonly_reason else "Editable (doble clic o F2)"
            return f"{self.headers[section]}  ·  {detail}\n{state}\nClic para ordenar"
        if orientation == Qt.Vertical and role == Qt.DisplayRole and section < len(self.rows):
            row = self.rows[section]
            return "+" if row.new else ("✕" if row.deleted else self.first_number + section)
        return super().headerData(section, orientation, role)

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False
        info = self._editable_column(index.column())
        row = self.rows[index.row()]
        if info is None or row.deleted:
            return False
        try:
            parsed = parse_value(value, info)
        except ValueError as e:
            self.rejected.emit(str(e))
            return False
        row[index.column()] = parsed
        self._row_changed(index.row())
        return True

    def _row_changed(self, r):
        self.dataChanged.emit(self.index(r, 0), self.index(r, len(self.headers) - 1))
        self.headerDataChanged.emit(Qt.Vertical, r, r)
        self.changed.emit()

    def _visible(self, row):
        return row.new or super()._visible(row)     # una fila nueva siempre se ve, aunque haya filtro

    # --- acciones
    def set_null(self, indexes):
        for index in indexes:
            self.setData(index, "", Qt.EditRole)

    def add_row(self, copy_of=None):
        """Agrega una fila nueva al final (vacía o copia de otra) y devuelve su número visible."""
        values = [None] * len(self.headers)
        if copy_of is not None:
            values = [v if self._editable_column(i) else None for i, v in enumerate(self.rows[copy_of])]
        self.beginResetModel()
        row = GridRow(values)
        self.all_rows.append(row)
        self._refresh(page=len(self.all_rows))          # a la última página, donde quedó la fila
        self.endResetModel()
        self.changed.emit()
        return next(i for i, r in enumerate(self.rows) if r is row)    # por identidad: filas iguales

    def delete_rows(self, visible_rows):
        """Marca las filas para borrar (o las desmarca). Una fila nueva se quita sin más."""
        self.beginResetModel()
        for r in sorted(set(visible_rows), reverse=True):
            row = self.rows[r]
            if row.new:
                self.all_rows = [r for r in self.all_rows if r is not row]   # por identidad, no por valor
            else:
                row.deleted = not row.deleted
        self._refresh()
        self.endResetModel()
        self.changed.emit()

    def has_changes(self):
        return any(r.new or r.deleted or r.changed_columns() for r in self.all_rows)

    def summary(self):
        changed = sum(1 for r in self.all_rows if not r.new and not r.deleted and r.changed_columns())
        new = sum(1 for r in self.all_rows if r.new)
        deleted = sum(1 for r in self.all_rows if r.deleted)
        return {"modificadas": changed, "nuevas": new, "borradas": deleted}

    key_kind, key_columns = None, []          # clave de la tabla (db.grid.fetch_key_columns), si se conoce

    def key_values(self):
        """{rowid: {columna clave: valor original}} para escribir el script por clave y no por ROWID. Vacío si la
        tabla no tiene clave o la consulta no trae todas sus columnas."""
        positions = {}
        for key in self.key_columns:
            i = next((i for i, c in enumerate(self.columns) if c is not None and c.name == key), None)
            if i is None:
                return {}
            positions[key] = i
        if not positions:
            return {}
        return {row.rowid: {k: row.original[i] for k, i in positions.items()}
                for row in self.all_rows if not row.new and row.rowid}

    def pending_ops(self):
        """Operaciones para guardar (ver db.grid.apply_changes). Revisa antes lo que Oracle rechazaría
        seguro (obligatorias vacías en filas nuevas) y lanza ValueError con todos los problemas."""
        problems, ops = [], []
        for row in self.all_rows:
            if row.deleted:
                if not row.new:
                    ops.append(("delete", row.rowid))
                continue
            if row.new:
                values = {}
                for i, info in enumerate(self.columns):
                    if info is None or info.readonly_reason:
                        continue
                    if row[i] is not None:
                        values[info.name] = row[i]
                    elif not info.nullable and not info.has_default:
                        problems.append(f"Fila nueva: falta {info.name} (es obligatoria)")
                ops.append(("insert", values))
                continue
            changed = row.changed_columns()
            if changed:
                ops.append(("update", row.rowid, {self.columns[i].name: row[i] for i in sorted(changed)}))
        if problems:
            raise ValueError("\n".join(dict.fromkeys(problems)))
        return ops

    def mark_saved(self, new_rowids):
        """Después de guardar: lo pendiente pasa a ser el estado leído."""
        self.beginResetModel()
        rowids = iter(new_rowids)
        self.all_rows = [r for r in self.all_rows if not r.deleted]
        for row in self.all_rows:
            if row.new:
                row.rowid = next(rowids, None)
            row.original = tuple(row)
        self._refresh()
        self.endResetModel()
        self.changed.emit()

    def revert(self):
        """Cancelar: descarta todo lo que no se guardó."""
        self.beginResetModel()
        self.all_rows = [r for r in self.all_rows if not r.new]
        for row in self.all_rows:
            row[:] = list(row.original)
            row.deleted = False
        self._refresh()
        self.endResetModel()
        self.changed.emit()
