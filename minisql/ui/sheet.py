"""Hoja de SQL: editor, ejecución y resultados."""

import re
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..sql.completion import completion_target, table_refs
from ..sql.editable import editable_target
from ..sql.text import (
    DDL,
    DML,
    changes_without_where,
    first_keyword,
    format_sql,
    split_script,
    statement_range,
)
from .editors import MonoText, SqlEditor
from .find_bar import with_find_bar
from .result_panel import ResultPanel
from .results import ResultModel
from .results_dock import ResultsDragger, placement
from .style import StatusLabel, variant
from .workers import Emitter


class SheetWidget(QWidget):
    """Hoja de SQL: editor con colores y autocompletado, y abajo los resultados y DBMS_OUTPUT.
    Ejecuta en la sesión principal de su conexión (conn_tab), en un hilo."""

    def __init__(self, conn_tab):
        super().__init__()
        self.conn_tab = conn_tab
        self.running = False

        self.editor = SqlEditor(conn_tab.completion)
        self.editor.setPlaceholderText(
            "Escribe SQL aquí.\n\nCtrl+Enter ejecuta la sentencia donde está el cursor "
            "(separa sentencias con una línea en blanco) o el texto seleccionado."
        )

        self.run_btn = variant(QPushButton("▶  Ejecutar"), "primary")
        self.run_btn.setToolTip("Ejecutar la sentencia donde está el cursor o el texto seleccionado  (Ctrl+Enter)")
        self.cancel_btn = variant(QPushButton("■  Cancelar"), "ghost")
        self.cancel_btn.setToolTip("Cancelar la ejecución en curso")
        self.cancel_btn.setEnabled(False)
        self.format_btn = variant(QPushButton("≡  Formatear"), "ghost")
        self.format_btn.setToolTip("Formatear la sentencia o la selección  (Ctrl+Shift+F)")
        self.status = StatusLabel("Listo")

        self.run_started = 0.0

        # resultados: filtro, orden y edición (si la consulta es de una sola tabla)
        self.results = ResultPanel(conn_tab)
        self.table = self.results.view
        self.model = ResultModel()
        self.table.setModel(self.model)
        self.last_sql = None

        # salida de DBMS_OUTPUT.PUT_LINE: se acumula entre ejecuciones, con la hora de cada una
        self.output_check = QCheckBox("DBMS_OUTPUT")
        self.output_check.setChecked(True)
        self.output_check.setToolTip("Mostrar lo que imprimen DBMS_OUTPUT.PUT_LINE los bloques y procedimientos")
        self.output = MonoText()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.output.setPlaceholderText("Aquí aparece lo que imprime DBMS_OUTPUT.PUT_LINE")
        clear_out = QToolButton()
        clear_out.setText("Limpiar salida")
        clear_out.setAutoRaise(True)
        clear_out.clicked.connect(self.clear_output)
        self.result_tabs = QTabWidget()
        self.result_tabs.setDocumentMode(True)
        self.result_tabs.addTab(self.results, "Resultados")
        self.result_tabs.addTab(self.output, "DBMS_OUTPUT")
        self.side_btn = QToolButton()
        self.side_btn.setAutoRaise(True)
        self.side_btn.clicked.connect(placement.toggle)
        corner = QWidget()
        corner_row = QHBoxLayout(corner)
        corner_row.setContentsMargins(0, 0, 0, 0)
        corner_row.setSpacing(2)
        corner_row.addWidget(clear_out)
        corner_row.addWidget(self.side_btn)
        self.result_tabs.setCornerWidget(corner, Qt.TopRightCorner)
        self.output_check.toggled.connect(lambda on: self.result_tabs.setTabVisible(1, on))
        self.last_output = 0

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(self.run_btn)
        bar.addWidget(self.cancel_btn)
        bar.addWidget(self.format_btn)
        bar.addSpacing(8)
        bar.addWidget(self.output_check)
        bar.addSpacing(12)
        bar.addWidget(self.status, 1)
        self.status.setObjectName("muted")
        self.output_check.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)    # nunca la tapa un mensaje largo
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # el mensaje cede espacio

        splitter = self.splitter = QSplitter(Qt.Vertical)
        splitter.setObjectName("editorSplit")             # su divisor se dibuja como una línea (theme.py)
        splitter.setChildrenCollapsible(False)
        editor_box = with_find_bar(self.editor)         # Ctrl+F buscar, Ctrl+H reemplazar
        self.find_bar = editor_box.find_bar
        splitter.addWidget(editor_box)
        splitter.addWidget(self.result_tabs)
        splitter.setSizes([300, 400])
        editor_box.setMinimumSize(220, 90)               # ningún lado se puede aplastar del todo
        self.result_tabs.setMinimumSize(300, 120)        # (y los resultados se compactan si quedan angostos)
        # resultados abajo o a la derecha: arrastrando sus pestañas o con el botón ⇆/⇅ (para todas las hojas)
        self.dragger = ResultsDragger(self.result_tabs.tabBar(), splitter)
        self.apply_results_position(placement.position)
        placement.changed.connect(self.apply_results_position)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 4)
        layout.setSpacing(6)
        layout.addLayout(bar)
        layout.addWidget(splitter, 1)

        self.emitter = Emitter()
        self.emitter.done.connect(self.on_done)
        self.emitter.failed.connect(self.on_error)
        self.emitter.output.connect(self.on_output)
        self.run_btn.clicked.connect(self.run)
        self.cancel_btn.clicked.connect(self.conn_tab.db.cancel)
        self.format_btn.clicked.connect(self.format_current)
        for seq, slot in (("Ctrl+Return", self.run), ("Ctrl+Enter", self.run),
                          ("Ctrl+Shift+F", self.format_current), ("F4", self.open_object_at_cursor)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

    def apply_results_position(self, position):
        right = position == "right"
        if (self.splitter.orientation() == Qt.Horizontal) != right:
            self.splitter.setOrientation(Qt.Horizontal if right else Qt.Vertical)
            size = self.splitter.width() if right else self.splitter.height()
            self.splitter.setSizes([size // 2, size - size // 2] if right else [size * 2 // 5, size - size * 2 // 5])
        self.side_btn.setText("⇅" if right else "⇆")
        self.side_btn.setToolTip("Resultados abajo" if right else "Resultados a la derecha"
                                 "  (también puedes arrastrar la pestaña Resultados)")

    def set_running(self, running, text=""):
        """Mientras corre, los resultados anteriores quedan tapados por "Ejecutando…" (no se pueden editar)."""
        self.running = running
        self.run_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        if running:
            self.set_status("")
            self.result_tabs.setCurrentWidget(self.results)
            self.results.set_busy(text or "Ejecutando…", self.conn_tab.db.cancel)
        else:
            self.results.set_busy(None)

    def open_object_at_cursor(self):
        """F4: abre el objeto cuyo nombre está bajo el cursor (acepta ESQUEMA.OBJETO y alias de tabla)."""
        text = self.editor.toPlainText()
        pos = self.editor.textCursor().position()
        tail = re.match(r"[\w$#]*", text[pos:]).group()
        start, end = statement_range(text, pos)
        quals, word = completion_target(text[start:pos] + tail)
        if not word:
            self.set_status("Pon el cursor sobre el nombre de un objeto y presiona F4")
            return
        name = word.upper()
        owner = quals[-1] if quals else None
        if owner is None:
            refs = table_refs(text[start:end])
            if name in refs:                 # un alias: abrir su tabla
                owner, name = refs[name]
        elif owner in table_refs(text[start:end]):
            owner, name = table_refs(text[start:end])[owner]   # alias.columna: abrir la tabla
        self.conn_tab.open_object_by_name(owner, name, self.set_status)

    def format_current(self):
        """Formatea el texto seleccionado o la sentencia donde está el cursor (se puede deshacer con Ctrl+Z)."""
        cur = self.editor.textCursor()
        if cur.hasSelection():
            start, end = cur.selectionStart(), cur.selectionEnd()
        else:
            start, end = statement_range(self.editor.toPlainText(), cur.position())
        doc_text = self.editor.toPlainText()
        original = doc_text[start:end]
        if not original.strip():
            return
        try:
            formatted = format_sql(original)
        except Exception as e:
            self.set_status(str(e), error=True)
            return
        lead = original[:len(original) - len(original.lstrip())]
        trail = original[len(original.rstrip()):]
        new_text = lead + formatted + trail
        if new_text == original:
            return
        cur.beginEditBlock()
        cur.setPosition(start)
        cur.setPosition(end, QTextCursor.KeepAnchor)
        cur.insertText(new_text)
        cur.endEditBlock()
        self.editor.setTextCursor(cur)

    def set_status(self, text, error=False):
        if error:
            self.status.error(text)
        else:
            self.status.show_message(text)

    def run(self):
        if self.running:
            return
        if not self.conn_tab.connected:
            if self.conn_tab.reconnecting:
                return
            r = QMessageBox.question(self, "Sin conexión",
                                     f"La conexión {self.conn_tab.name} está cerrada.\n\n¿Reconectar y ejecutar?")
            if r == QMessageBox.Yes:
                self.conn_tab.reconnect(then=self.run)
            return
        cur = self.editor.textCursor()
        if cur.hasSelection():
            start, end = cur.selectionStart(), cur.selectionEnd()
            sql = cur.selectedText().replace("\u2029", "\n")
        else:
            text = self.editor.toPlainText()
            start, end = statement_range(text, cur.position())
            sql = text[start:end]
        serveroutput, statements = split_script(sql)    # una o varias sentencias (';' o '/' las separan)
        if serveroutput is not None:              # SET SERVEROUTPUT ON|OFF: la casilla DBMS_OUTPUT
            self.output_check.setChecked(serveroutput)
            if not statements:
                self.set_status(f"DBMS_OUTPUT {'activado' if serveroutput else 'desactivado'}")
                return
        if not statements:
            self.set_status("No hay ninguna sentencia donde está el cursor")
            return
        self.show_running_statement(start, end)      # siempre a la vista lo que se va a ejecutar
        if self.results.has_unsaved_changes() and QMessageBox.question(
                self, "Cambios sin guardar",
                "Hay cambios sin guardar en los resultados. Si ejecutas, se pierden.\n\n¿Descartarlos y ejecutar?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if not self.confirm(statements):
            return
        self.last_output = 0
        self.result_tabs.setTabText(1, "DBMS_OUTPUT")
        script = len(statements) > 1
        running = f"Ejecutando {len(statements)} sentencias…" if script else "Ejecutando…"
        self.set_running(True, running if not self.conn_tab.db.busy
                         else "Esperando a que termine otra hoja de esta conexión…")
        if script:
            self.last_sql = None                  # resultados de un script: solo lectura
            self.conn_tab.run_script(statements, self.emitter)
            return
        sql = self.last_sql = statements[0]
        editable, _why = editable_target(sql)
        self.conn_tab.run_statement(sql, self.emitter, with_rowid=editable is not None)

    RUN_FLASH_MS = 1500

    def show_running_statement(self, start, end):
        """Resalta un momento la sentencia que se ejecuta y la trae a la vista: así nunca corre algo
        distinto de lo que se ve (p. ej. con el cursor fuera de pantalla al recuperar la sesión)."""
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor("#1f4f3f" if self.editor.dark else "#d6f5e3"))
        sel.cursor = cursor
        self.editor.set_highlights("run", [sel])
        view = QTextCursor(self.editor.document())
        view.setPosition(start)
        if not self.editor.viewport().rect().contains(self.editor.cursorRect(view).topLeft()):
            keep = self.editor.textCursor()
            self.editor.setTextCursor(view)           # llevar la sentencia a la vista…
            self.editor.centerCursor()
            self.editor.setTextCursor(keep)           # …sin perder la selección ni la posición del cursor
        QTimer.singleShot(self.RUN_FLASH_MS, self.editor, lambda: self.editor.set_highlights("run", []))

    def confirm(self, statements):
        """Pide confirmación si alguna sentencia es peligrosa. statements: lista (o una sola sentencia)."""
        if isinstance(statements, str):
            statements = [statements]
        sql = ";\n".join(statements)
        kws = [first_keyword(s) for s in statements]
        kw = next((k for k in kws if k in ("DROP", "TRUNCATE")), next((k for k in kws if k in DDL), kws[0]))
        dml_before_ddl = any(k in DML for k in kws[:kws.index(kw)]) if kw in DDL else False
        unguarded = changes_without_where(sql)      # también dentro de BEGIN … END y sin creerle a subqueries
        if unguarded:
            listed = "\n".join(f"  • {u}" for u in unguarded[:5]) + ("\n  • …" if len(unguarded) > 5 else "")
            msg = ("Sin WHERE: esto afectará TODAS las filas de la tabla.\n\n"
                   f"{listed}\n\n¿Ejecutar de todas formas?")
        elif kw in ("DROP", "TRUNCATE"):
            msg = f"{kw} no se puede deshacer con Rollback.\n\n¿Ejecutar?"
        elif kw in DDL and (self.conn_tab.pending or dml_before_ddl):
            where = "en esta conexión" if self.conn_tab.pending else "de las sentencias anteriores del script"
            msg = (f"Hay cambios sin confirmar {where} y las sentencias DDL "
                   "hacen COMMIT automático de ellos.\n\n¿Continuar?")
        else:
            return True
        preview = sql if len(sql) < 400 else sql[:400] + "…"
        r = QMessageBox.warning(self, "Confirmar", f"{msg}\n\n{preview}",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return r == QMessageBox.Yes

    def on_output(self, lines):
        if not self.output_check.isChecked():
            return
        self.last_output = len(lines)
        if self.output.toPlainText():
            self.output.appendPlainText("")
        self.output.appendPlainText(f"-- {time.strftime('%H:%M:%S')} · {len(lines)} línea(s)")
        self.output.appendPlainText("\n".join(lines))
        self.result_tabs.setTabText(1, f"DBMS_OUTPUT ({len(lines)})")

    def clear_output(self):
        self.output.clear()
        self.last_output = 0
        self.result_tabs.setTabText(1, "DBMS_OUTPUT")

    def on_done(self, res):
        self.set_running(False)
        self.conn_tab.after_statement(res)
        if "headers" not in res and self.last_output:
            self.result_tabs.setCurrentWidget(self.output)   # un bloque que imprimió: mostrar su salida
        elif "headers" in res:
            self.result_tabs.setCurrentWidget(self.results)
        if "headers" in res:
            self.model = self.results.show_result(res, self.last_sql)
            more = " (primera página: ▶ trae la siguiente)" if res.get("more") else ""
            script = f"{res['message']}  ·  última consulta: " if res.get("script") else ""
            self.set_status(f"{script}{len(res['rows'])} filas{more}  ·  {res['elapsed']:.2f} s")
        else:
            self.set_status(f"{res['message']}  ·  {res['elapsed']:.2f} s")

    def on_error(self, msg):
        self.set_running(False)
        self.conn_tab.refresh_pending()             # en un script, las sentencias anteriores sí se ejecutaron
        if self.last_output:
            self.result_tabs.setCurrentWidget(self.output)
        self.set_status(msg, error=True)
