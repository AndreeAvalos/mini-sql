"""Tabla de resultados."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from ..config import MAX_CELL_CHARS
from .style import MUTED_COLOR


class ResultModel(QAbstractTableModel):
    def __init__(self, headers=None, rows=None):
        super().__init__()
        self.headers = headers or []
        self.rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        v = self.rows[index.row()][index.column()]
        if role == Qt.DisplayRole:
            if v is None:
                return "(null)"
            if isinstance(v, bytes):
                return f"<{len(v)} bytes>"
            s = str(v)
            return s if len(s) <= MAX_CELL_CHARS else s[:MAX_CELL_CHARS] + "…"
        if role == Qt.ForegroundRole and v is None:
            return QColor(MUTED_COLOR)
        if role == Qt.TextAlignmentRole and isinstance(v, (int, float)) and not isinstance(v, bool):
            return Qt.AlignRight | Qt.AlignVCenter
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        return self.headers[section] if orientation == Qt.Horizontal else section + 1


class ResultView(QTableView):
    def __init__(self):
        super().__init__()
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.verticalHeader().setDefaultSectionSize(22)
        # Al ajustar columnas, medir solo las primeras filas: medir 1000 filas traba la interfaz.
        self.horizontalHeader().setResizeContentsPrecision(100)
        self.verticalHeader().setResizeContentsPrecision(100)

    def show_rows(self, headers, rows):
        model = ResultModel(headers, rows)
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
