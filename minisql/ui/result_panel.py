"""Panel de resultados: filtro, ordenamiento y, si la consulta es de una sola tabla, edición con Guardar/Cancelar.

Los cambios se guardan por ROWID en la sesión principal y quedan pendientes de Commit, como cualquier DML.
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..db.catalog import resolve_object
from ..db.grid import fetch_columns_info, preview_sql
from ..sql.editable import editable_target
from .results import EditableResultModel, ResultModel, ResultView
from .save_preview import SavePreviewDialog
from .style import ERROR_COLOR, MUTED_COLOR, OK_COLOR, WARN_COLOR


def _button(text, tip, slot):
    b = QToolButton()
    b.setText(text)
    b.setToolTip(tip)
    b.setAutoRaise(True)
    b.clicked.connect(slot)
    return b


class ResultPanel(QWidget):
    def __init__(self, conn_tab):
        super().__init__()
        self.conn_tab = conn_tab
        self.target = None             # (dueño, tabla) si los resultados se pueden editar
        self.saving = False

        self.view = ResultView()
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu)
        self.view.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                  | QAbstractItemView.AnyKeyPressed)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filtrar: texto · =valor · >100 · <=2026-01-31 · null · !null")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setMinimumWidth(170)
        self.filter_column = QComboBox()
        self.filter_column.setToolTip("Columna donde filtrar")
        self.filter_column.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.count = QLabel("")
        self.count.setStyleSheet(f"color: {MUTED_COLOR};")
        self.state = QLabel("")
        self.state.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.state.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)   # el estado se ve completo

        self.add_btn = _button("+ Fila", "Agregar una fila nueva", self.add_row)
        self.dup_btn = _button("⧉ Duplicar", "Duplicar la fila seleccionada", self.duplicate_row)
        self.del_btn = _button("✕ Borrar", "Marcar las filas seleccionadas para borrar (otra vez: desmarcar)",
                               self.delete_rows)
        self.save_btn = _button("✔ Guardar", "Guardar los cambios en la tabla (Ctrl+S). Quedan pendientes de Commit",
                                self.save)
        self.cancel_btn = _button("✖ Cancelar", "Descartar los cambios que no se guardaron", self.cancel)
        self.edit_buttons = (self.add_btn, self.dup_btn, self.del_btn)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.filter_edit, 1)                 # el filtro cede espacio si falta
        bar.addWidget(self.filter_column)
        bar.addWidget(self.count)
        bar.addSpacing(12)
        bar.addWidget(self.state)
        for b in (*self.edit_buttons, self.save_btn, self.cancel_btn):
            bar.addWidget(b)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(bar)
        layout.addWidget(self.view, 1)

        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(150)
        self.filter_timer.timeout.connect(self.apply_filter)
        self.filter_edit.textChanged.connect(lambda _t: self.filter_timer.start())
        self.filter_column.currentIndexChanged.connect(lambda _i: self.apply_filter())
        for keys, slot in (("Ctrl+S", self.save), ("Ctrl+F", self._focus_filter)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)
        self._set_state("", "")
        self._update_buttons()

    # --- mostrar resultados
    @property
    def model(self):
        return self.view.model()

    def show_result(self, res, sql=None):
        """Muestra el resultado de una consulta. Si trae ROWID y es de una sola tabla, se puede editar."""
        headers, rows = res["headers"], res["rows"]
        target, reason = editable_target(sql) if sql else (None, None)
        if res.get("rowids") is not None and target is not None:
            model = EditableResultModel(headers, rows, res["rowids"], self.view.theme)
            model.changed.connect(self._update_buttons)
            model.changed.connect(self._update_count)
            model.rejected.connect(lambda msg: self._set_state(f"✖ {msg}", ERROR_COLOR))
            self._load_columns(model, target)
            self._set_state("Revisando si se puede editar…", MUTED_COLOR)
        else:
            model = ResultModel(headers, rows, self.view.theme)
            if res.get("not_editable"):           # Oracle no dio el ROWID (p. ej. ORA-01446)
                reason = "Oracle no permite identificar las filas de este resultado"
            self._set_state(f"🔒 Solo lectura: {reason}" if reason else "", MUTED_COLOR)
        self.target = None
        self.view.setModel(model)
        self.view.fit_columns()
        self._reset_filter_columns(headers)
        self.apply_filter()
        self._update_buttons()
        return model

    def _load_columns(self, model, target):
        me = self.conn_tab.db.schema

        def fn(cur):
            found = resolve_object(cur, me, target.owner, target.table)
            if not found:
                return None, f"no encontré la tabla {target.table}"
            owner, name, otype = found
            if otype != "TABLE":
                return None, f"{owner}.{name} es {otype.lower()}, no una tabla"
            return (owner, name, fetch_columns_info(cur, owner, name)), None

        def ok(result):
            found, reason = result
            if self.model is not model:
                return                           # llegó otro resultado mientras tanto
            if not found:
                self._set_state(f"🔒 Solo lectura: {reason}", MUTED_COLOR)
                return
            owner, name, columns = found
            model.set_columns([columns.get(target.source_of(h, columns) or "") for h in model.headers])
            if model.editable:
                self.target = (owner, name)
                self._set_state(f"✎ {owner}.{name}", OK_COLOR,
                                "Editable: doble clic o F2 para editar una celda; vacío = NULL.\n"
                                "Guardar (Ctrl+S) deja los cambios pendientes de Commit.")
            else:
                self._set_state("🔒 Solo lectura: ninguna columna del resultado se puede editar", MUTED_COLOR)
            self._update_buttons()

        self.conn_tab.meta_call(fn, ok, lambda msg: self._set_state(f"🔒 Solo lectura: {msg}", MUTED_COLOR))

    # --- filtro
    def _reset_filter_columns(self, headers):
        self.filter_column.blockSignals(True)
        current = self.filter_column.currentText()
        self.filter_column.clear()
        self.filter_column.addItem("Todas las columnas")
        self.filter_column.addItems(headers)
        index = self.filter_column.findText(current)
        self.filter_column.setCurrentIndex(index if index > 0 else 0)
        self.filter_column.blockSignals(False)

    def apply_filter(self):
        model = self.model
        if model is None:
            return
        column = self.filter_column.currentIndex() - 1
        model.set_filter(self.filter_edit.text(), column if column >= 0 else None)
        self._update_count()

    def _update_count(self):
        model = self.model
        shown, total = len(model.rows), len(model.all_rows)
        self.count.setText(f"{shown} de {total} filas" if shown != total else (f"{total} filas" if total else ""))

    def _focus_filter(self):
        self.filter_edit.setFocus()
        self.filter_edit.selectAll()

    # --- edición
    def _editable_model(self):
        model = self.model
        return model if isinstance(model, EditableResultModel) and self.target else None

    def has_unsaved_changes(self):
        model = self.model
        return isinstance(model, EditableResultModel) and model.has_changes()

    def _selected_rows(self):
        return sorted({i.row() for i in self.view.selectionModel().selectedIndexes()}) if self.model else []

    def add_row(self):
        model = self._editable_model()
        if model:
            self._go_to_new(model.add_row())

    def duplicate_row(self):
        model, rows = self._editable_model(), self._selected_rows()
        if model and rows:
            self._go_to_new(model.add_row(copy_of=rows[0]))

    def _go_to_new(self, row):
        model = self.model
        first = next((i for i, c in enumerate(model.columns) if c is not None and not c.readonly_reason), 0)
        index = model.index(row, first)
        self.view.scrollTo(index)
        self.view.setCurrentIndex(index)
        self.view.edit(index)

    def delete_rows(self):
        model, rows = self._editable_model(), self._selected_rows()
        if model and rows:
            model.delete_rows(rows)

    def set_null(self):
        model = self._editable_model()
        if model:
            model.set_null(self.view.selectionModel().selectedIndexes())

    def cancel(self):
        model = self._editable_model()
        if model and model.has_changes():
            model.revert()
            self._set_state(f"✎ {'.'.join(self.target)} · cambios descartados", OK_COLOR)

    def save(self):
        model = self._editable_model()
        if model is None or not model.has_changes() or self.saving:
            return
        try:
            ops = model.pending_ops()
        except ValueError as e:
            QMessageBox.warning(self, "No se puede guardar", str(e))
            return
        summary = model.summary()
        owner, table = self.target
        if not self.confirm_save(preview_sql(owner, table, ops), summary):
            return
        self.saving = True
        self._update_buttons()
        self._set_state("Guardando…", MUTED_COLOR)

        def ok(result):
            self.saving = False
            model.mark_saved(result["rowids"])
            txn = result.get("txn")
            self.conn_tab.set_pending(True if txn is None else bool(txn))
            done = ", ".join(f"{n} {k}" for k, n in summary.items() if n)
            self._set_state(f"✔ Guardado ({done}) · pendiente de Commit", WARN_COLOR)
            self._update_buttons()

        def failed(msg):
            self.saving = False
            self._set_state(f"✖ No se guardó nada: {msg.splitlines()[0]}", ERROR_COLOR)
            self.state.setToolTip(msg)
            self._update_buttons()

        self.conn_tab.runner.run(lambda: self.conn_tab.db.apply_grid_changes(owner, table, ops), ok, failed)

    def confirm_save(self, statements, summary):
        """Muestra las sentencias que se van a ejecutar y devuelve True si el usuario confirma."""
        dialog = SavePreviewDialog(".".join(self.target), statements, summary, self)
        return dialog.exec() == QDialog.Accepted

    # --- estado y botones
    def _set_state(self, text, color, tip=None):
        self.state.setText(text)
        self.state.setToolTip(text + (f"\n\n{tip}" if tip else ""))
        self.state.setStyleSheet(f"color: {color};" if color else "")

    def _update_buttons(self):
        editable = self._editable_model() is not None and not self.saving
        changes = editable and self.model.has_changes()
        for b in self.edit_buttons:
            b.setVisible(self._editable_model() is not None)
            b.setEnabled(editable)
        self.save_btn.setVisible(self._editable_model() is not None)
        self.cancel_btn.setVisible(self._editable_model() is not None)
        self.save_btn.setEnabled(bool(changes))
        self.cancel_btn.setEnabled(bool(changes))

    def _menu(self, pos):
        menu = QMenu(self)
        menu.addAction("Copiar  (Ctrl+C)", self.view.copy_selection)
        if self._editable_model():
            menu.addSeparator()
            menu.addAction("Establecer NULL", self.set_null)
            menu.addAction("Agregar fila", self.add_row)
            menu.addAction("Duplicar fila", self.duplicate_row)
            menu.addAction("Borrar / restaurar fila(s)", self.delete_rows)
            if self.model.has_changes():
                menu.addSeparator()
                menu.addAction("Guardar cambios  (Ctrl+S)", self.save)
                menu.addAction("Cancelar cambios", self.cancel)
        menu.exec(self.view.viewport().mapToGlobal(pos))
