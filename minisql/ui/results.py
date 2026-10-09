"""Tabla de resultados: encabezado destacado, un color por tipo de dato y ordenar con clic en el encabezado."""
import datetime
import numbers

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeySequence
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from ..config import MAX_CELL_CHARS
from .style import RESULT_THEME, result_stylesheet, result_theme


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


def display_text(value):
    """Cómo se ve un valor en la celda."""
    if value is None:
        return "(null)"
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
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


class ResultModel(QAbstractTableModel):
    def __init__(self, headers=None, rows=None, theme=None):
        super().__init__()
        self.headers = headers or []
        self.rows = list(rows or [])
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
            return section + 1
        if role == Qt.TextAlignmentRole:
            return Qt.AlignRight | Qt.AlignVCenter
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        """Clic en el encabezado. Los nulos quedan siempre al final."""
        if not 0 <= column < len(self.headers):
            return
        self.layoutAboutToBeChanged.emit()
        values = [r for r in self.rows if r[column] is not None]
        nulls = [r for r in self.rows if r[column] is None]
        values.sort(key=lambda r: sort_key(r[column]), reverse=order == Qt.DescendingOrder)
        self.rows = values + nulls
        self.layoutChanged.emit()


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
        self.verticalHeader().setDefaultSectionSize(22)
        # Al ajustar columnas, medir solo las primeras filas: medir 1000 filas traba la interfaz.
        header.setResizeContentsPrecision(100)
        self.verticalHeader().setResizeContentsPrecision(100)
        self._unsorted()
        self.setSortingEnabled(True)

    def _unsorted(self):
        """Sin columna de orden: los resultados quedan en el orden de la consulta (su ORDER BY)."""
        self.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)

    def setModel(self, model):
        self._unsorted()                 # cada resultado nuevo llega en su orden, sin reordenar
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
                vals.append("" if v is None else str(v).replace("\t", " ").replace("\n", " "))
            lines.append("\t".join(vals))
        QGuiApplication.clipboard().setText("\n".join(lines))
