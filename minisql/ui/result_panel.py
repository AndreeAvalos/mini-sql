"""Panel de resultados: filtro, ordenamiento y, si la consulta es de una sola tabla, edición con Guardar/Cancelar.

Los cambios se guardan por ROWID en la sesión principal y quedan pendientes de Commit, como cualquier DML.
"""
import threading

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
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import MAX_ROWS, PAGE_ROWS
from ..db.catalog import resolve_object
from ..db.grid import fetch_columns_info, preview_sql
from ..sql.editable import editable_target
from .busy_overlay import BusyOverlay
from .results import EditableResultModel, ResultModel, ResultView
from .save_preview import SavePreviewDialog
from .style import ERROR_COLOR, MUTED_COLOR, OK_COLOR, WARN_COLOR
from .theme import tokens


def _button(text, tip, slot):
    b = QToolButton()
    b.setText(text)
    b.setToolTip(tip)
    b.setAutoRaise(True)
    b.clicked.connect(slot)
    return b


class ResultMessage(QWidget):
    """Lo que hizo una sentencia que no trae filas ("3 filas actualizadas") o su error, grande y en el lugar
    de la tabla: ahí es donde se mira después de ejecutar."""

    def __init__(self):
        super().__init__()
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignCenter)
        self.title = QLabel()
        self.detail = QLabel()
        self.detail.setObjectName("muted")
        for label in (self.title, self.detail):
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignCenter)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 16, 24, 16)
        box.addStretch(1)
        box.addWidget(self.icon)
        box.addWidget(self.title)
        box.addWidget(self.detail)
        box.addStretch(2)

    def set_message(self, title, detail="", error=False):
        colors = tokens()
        color = colors["danger"] if error else colors["success"]
        self.icon.setText("✖" if error else "✔")
        self.icon.setStyleSheet(f"color: {color}; font-size: 26pt;")
        self.title.setStyleSheet(f"font-size: 13pt; font-weight: 600; color: {color if error else colors['text']};")
        self.title.setText(title)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))


class ResultPanel(QWidget):
    def __init__(self, conn_tab):
        super().__init__()
        self.conn_tab = conn_tab
        self.target = None             # (dueño, tabla) si los resultados se pueden editar
        self.saving = False
        self.stream = None             # cursor abierto en Oracle para traer las páginas siguientes
        self.fetching = False
        self.saved_uncommitted = False # se guardaron cambios de la grilla que esperan Commit/Rollback
        conn_tab.transaction_ended.connect(self._transaction_ended)

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
        self.count.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)   # el contador no se corta
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
        self.prev_btn = _button("◀", "Página anterior", self.prev_page)
        self.page_label = QLabel("")
        self.next_btn = _button("▶", f"Página siguiente (trae {PAGE_ROWS} filas más de Oracle si hace falta)",
                                self.next_page)
        self.all_btn = _button("⏬ Todas", f"Traer todas las filas que faltan (hasta {MAX_ROWS:,})".replace(",", "."),
                               self.fetch_all)
        self.prev_btn.setArrowType(Qt.LeftArrow)
        self.next_btn.setArrowType(Qt.RightArrow)
        self.paging = (self.prev_btn, self.page_label, self.next_btn, self.all_btn)
        for w in self.paging:
            w.hide()                                       # sin resultados todavía, no hay páginas

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.filter_edit, 1)                 # el filtro cede espacio si falta
        bar.addWidget(self.filter_column)
        bar.addSpacing(8)
        bar.addWidget(self.state)
        for b in (*self.edit_buttons, self.save_btn, self.cancel_btn):
            bar.addWidget(b)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(bar)
        self.message = ResultMessage()                    # en lugar de la tabla: "3 filas actualizadas" o el error
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.message)
        layout.addWidget(self.stack, 1)
        footer = QHBoxLayout()                             # debajo de la tabla: cuántas filas y las páginas
        footer.setContentsMargins(4, 0, 0, 0)
        footer.addWidget(self.count)
        footer.addSpacing(8)
        for w in self.paging:
            footer.addWidget(w)
        footer.addStretch(1)
        layout.addLayout(footer)
        self.overlay = BusyOverlay(self)                   # "Ejecutando…" encima, bloquea lo anterior

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

    # --- ocupado: ejecutando, guardando o trayendo filas
    @property
    def busy(self):
        return self.overlay.active

    def set_busy(self, text, on_cancel=None):
        """text: qué se está haciendo (tapa los resultados y no deja tocarlos); None: terminó.
        on_cancel: si se da, la capa muestra un botón Cancelar."""
        if text:
            self.filter_timer.stop()
            self.overlay.start(text, on_cancel)
        else:
            self.overlay.stop()
        for w in (self.view, self.filter_edit, self.filter_column):
            w.setEnabled(not text)                         # tampoco con el teclado (F2, escribir, Ctrl+C…)
        self._update_buttons()
        if self.model is not None:
            self._update_count()

    # --- mostrar resultados
    # --- angosto (p. ej. resultados a la derecha): botones solo con su símbolo, filtro más corto
    COMPACT_WIDTH = 900
    compact = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.set_compact(self.width() < self.COMPACT_WIDTH)

    def set_compact(self, compact):
        if compact == self.compact:
            return
        self.compact = compact
        for button in (self.add_btn, self.dup_btn, self.del_btn, self.save_btn, self.cancel_btn):
            full = button.property("full_text") or button.text()
            button.setProperty("full_text", full)
            button.setText(full.split(" ", 1)[0] if compact else full)
        self.filter_edit.setMinimumWidth(90 if compact else 170)
        self.filter_column.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon if compact
                                               else QComboBox.AdjustToContents)
        self.filter_column.setMinimumContentsLength(8)
        # el estado (✎ TABLA / 🔒 motivo) cede espacio; completo en su tooltip
        self.state.setSizePolicy(QSizePolicy.Ignored if compact else QSizePolicy.Minimum, QSizePolicy.Preferred)

    @property
    def model(self):
        return self.view.model()

    def _show_filter(self, visible):
        self.filter_edit.setVisible(visible)
        self.filter_column.setVisible(visible)

    def show_message(self, title, detail="", error=False):
        """Una sentencia sin filas (UPDATE, DDL, PL/SQL…) o un error: se muestra en lugar de la tabla anterior."""
        self._close_stream()
        self.target = None
        self.view.setModel(ResultModel([], [], self.view.theme, PAGE_ROWS))
        self._set_state("", "")
        self.message.set_message(title, detail, error)
        self.stack.setCurrentWidget(self.message)
        self._show_filter(False)                         # sin tabla no hay nada que filtrar
        self._update_buttons()
        self._update_count()

    def show_result(self, res, sql=None):
        """Muestra el resultado de una consulta. Si trae ROWID y es de una sola tabla, se puede editar."""
        self.stack.setCurrentWidget(self.view)
        self._show_filter(True)
        headers, rows = res["headers"], res["rows"]
        target, reason = editable_target(sql) if sql else (None, None)
        self._close_stream()
        self.stream, self.fetching = res.get("stream"), False
        self.saved_uncommitted = False
        if res.get("rowids") is not None and target is not None:
            model = EditableResultModel(headers, rows, res["rowids"], self.view.theme, PAGE_ROWS)
            model.changed.connect(self._update_buttons)
            model.changed.connect(self._update_count)
            model.rejected.connect(lambda msg: self._set_state(f"✖ {msg}", ERROR_COLOR))
            self._load_columns(model, target)
            self._set_state("Revisando si se puede editar…", MUTED_COLOR)
        else:
            model = ResultModel(headers, rows, self.view.theme, PAGE_ROWS)
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
        more = self.has_more()
        loaded, matching = len(model.all_rows), len(model.filtered)
        plus = "+" if more else ""
        if model.matcher is not None:
            text = f"{matching} de {loaded}{plus} filas"           # las que cumplen el filtro
        else:
            text = f"{loaded}{plus} filas" if loaded or more else ""
        if model.rows and (more or model.page_count() > 1):      # varias páginas: qué filas se ven
            first = model.first_number
            text = f"{first}-{first + len(model.rows) - 1} · {text}"
        self.count.setText(text)
        self.count.setToolTip("El filtro y el orden se aplican a las filas ya cargadas. "
                              "⏬ Todas trae las que faltan." if more else "")
        self._update_paging()

    # --- páginas
    def has_more(self):
        """¿Quedan filas en Oracle sin traer?"""
        return self.stream is not None and not self.stream.done

    def _update_paging(self):
        model = self.model
        if model is None:
            return
        more, pages = self.has_more(), model.page_count()
        visible = more or pages > 1
        for w in self.paging:
            w.setVisible(visible)
        self.page_label.setText(f"Pág. {model.page + 1}/{pages}{'+' if more else ''}")
        idle = not self.fetching and not self.busy
        self.prev_btn.setEnabled(idle and model.page > 0)
        self.next_btn.setEnabled(idle and (model.page < pages - 1 or more))
        self.all_btn.setEnabled(idle and more and len(model.all_rows) < MAX_ROWS)

    def prev_page(self):
        model = self.model
        if model is not None and model.page > 0 and not self.busy:
            model.set_page(model.page - 1)
            self._update_count()

    def next_page(self):
        model = self.model
        if model is None or self.fetching or self.busy:
            return
        if model.page < model.page_count() - 1:
            model.set_page(model.page + 1)
            self._update_count()
        elif self.has_more():
            self._fetch(PAGE_ROWS)

    def fetch_all(self):
        if self.has_more() and not self.fetching and not self.busy:
            self._fetch(None)

    def _fetch(self, n):
        """Trae n filas más (o todas las que faltan, hasta MAX_ROWS) en un hilo."""
        stream, model = self.stream, self.model
        limit = MAX_ROWS - len(model.all_rows)
        self.fetching = True
        self.set_busy("Trayendo filas…", self.conn_tab.db.cancel)

        def ok(result):
            if self.model is not model:
                return                                   # llegó otro resultado mientras tanto
            self.fetching = False
            self.set_busy(None)
            rows, rowids = result
            page = model.page
            if rows:
                model.append_rows(rows, rowids)
            if n and model.page_count() - 1 > page:
                model.set_page(page + 1)                 # ▶: mostrar la página que se acaba de traer
            if not stream.done and len(model.all_rows) >= MAX_ROWS:
                self._set_state(f"Se cargó el máximo de {MAX_ROWS:,} filas".replace(",", "."), MUTED_COLOR)
            self._update_count()

        def failed(msg):
            if self.model is not model:
                return
            self.fetching = False
            self.set_busy(None)
            self._set_state(f"✖ No se pudieron traer más filas: {msg.splitlines()[0]}", ERROR_COLOR, msg)
            self._update_count()

        fn = (lambda: stream.fetch(n)) if n else (lambda: stream.fetch_all(limit))
        self.conn_tab.runner.run(fn, ok, failed)

    def _close_stream(self):
        stream, self.stream = self.stream, None
        if stream is not None and not stream.done:
            # cerrarlo toma el candado de la sesión: en un hilo, por si otra hoja está ejecutando
            threading.Thread(target=stream.close, daemon=True).start()

    def forget_stream(self):
        """La sesión se cerró: el cursor abierto ya no sirve (no hay nada que cerrar en Oracle)."""
        if self.stream is not None:
            self.stream.done = True
            self.stream = None
            if self.model is not None:
                self._update_count()

    def dispose(self):
        """La hoja o el visor se cierra: libera el cursor abierto en Oracle."""
        self._close_stream()

    def _focus_filter(self):
        self.filter_edit.setFocus()
        self.filter_edit.selectAll()

    # --- edición
    def _editable_model(self):
        model = self.model
        if self.busy:
            return None                                  # ocupado: lo que se ve está por cambiar
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
        self.set_busy("Guardando…")

        def ok(result):
            self.saving = False
            self.set_busy(None)
            model.mark_saved(result["rowids"])
            txn = result.get("txn")
            self.conn_tab.set_pending(True if txn is None else bool(txn))
            done = ", ".join(f"{n} {k}" for k, n in summary.items() if n)
            self._set_state(f"✔ Guardado ({done}) · pendiente de Commit", WARN_COLOR)
            self.saved_uncommitted = True
            self._update_buttons()

        def failed(msg):
            self.saving = False
            self.set_busy(None)
            self._set_state(f"✖ No se guardó nada: {msg.splitlines()[0]}", ERROR_COLOR)
            self.state.setToolTip(msg)
            self._update_buttons()

        self.conn_tab.runner.run(lambda: self.conn_tab.db.apply_grid_changes(owner, table, ops), ok, failed)

    def confirm_save(self, statements, summary):
        """Muestra las sentencias que se van a ejecutar y devuelve True si el usuario confirma."""
        dialog = SavePreviewDialog(".".join(self.target), statements, summary, self)
        return dialog.exec() == QDialog.Accepted

    def _transaction_ended(self, how):
        """Commit o Rollback en la conexión: el aviso "pendiente de Commit" ya no vale."""
        if not self.saved_uncommitted:
            return
        self.saved_uncommitted = False
        if how == "Commit":
            self._set_state("✔ Cambios confirmados (Commit)", OK_COLOR)
        else:
            self._set_state("↺ Rollback: se deshicieron los cambios guardados. Vuelve a ejecutar la consulta "
                            "para ver los datos actuales", WARN_COLOR)

    # --- estado y botones
    def _set_state(self, text, color, tip=None):
        self.state.setText(text)
        self.state.setToolTip(text + (f"\n\n{tip}" if tip else ""))
        self.state.setStyleSheet(f"color: {color};" if color else "")

    def _update_buttons(self):
        shown = isinstance(self.model, EditableResultModel) and self.target is not None   # aunque esté ocupado
        editable = self._editable_model() is not None and not self.saving
        changes = editable and self.model.has_changes()
        for b in (*self.edit_buttons, self.save_btn, self.cancel_btn):
            b.setVisible(shown)
        for b in self.edit_buttons:
            b.setEnabled(editable)
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
