"""Hoja de SQL: editor, ejecución y resultados."""

import re
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import MAX_ROWS
from ..sql.completion import completion_target, table_refs
from ..sql.text import (
    DDL,
    clean_statement,
    first_keyword,
    format_sql,
    sqlplus_to_sql,
    statement_at_cursor,
    statement_range,
    strip_comments,
)
from .editors import SqlEditor
from .results import ResultModel, ResultView
from .style import StatusLabel, mono_font
from .workers import Emitter


class SheetWidget(QWidget):
    """Hoja de SQL: editor con colores y autocompletado, y abajo los resultados y DBMS_OUTPUT.
    Ejecuta en la sesión principal de su conexión (conn_tab), en un hilo."""
    SPINNER = "◐◓◑◒"

    def __init__(self, conn_tab):
        super().__init__()
        self.conn_tab = conn_tab
        self.running = False

        self.editor = SqlEditor(conn_tab.completion)
        self.editor.setPlaceholderText(
            "Escribe SQL aquí.\n\nCtrl+Enter ejecuta la sentencia donde está el cursor "
            "(separa sentencias con una línea en blanco) o el texto seleccionado."
        )

        self.run_btn = QPushButton("▶ Ejecutar  (Ctrl+Enter)")
        self.cancel_btn = QPushButton("■ Cancelar")
        self.cancel_btn.setEnabled(False)
        self.format_btn = QPushButton("≡ Formatear  (Ctrl+Shift+F)")
        self.status = StatusLabel("Listo")

        # animación mientras se ejecuta: spinner + segundos transcurridos
        self.spin_timer = QTimer(self)
        self.spin_timer.setInterval(120)
        self.spin_timer.timeout.connect(self._tick)
        self.spin_frame = 0
        self.run_started = 0.0
        self.run_text = ""

        self.model = ResultModel()
        self.table = ResultView()
        self.table.setModel(self.model)

        # salida de DBMS_OUTPUT.PUT_LINE: se acumula entre ejecuciones, con la hora de cada una
        self.output_check = QCheckBox("DBMS_OUTPUT")
        self.output_check.setChecked(True)
        self.output_check.setToolTip("Mostrar lo que imprimen DBMS_OUTPUT.PUT_LINE los bloques y procedimientos")
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(mono_font())
        self.output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.output.setPlaceholderText("Aquí aparece lo que imprime DBMS_OUTPUT.PUT_LINE")
        clear_out = QToolButton()
        clear_out.setText("Limpiar salida")
        clear_out.clicked.connect(self.clear_output)
        self.result_tabs = QTabWidget()
        self.result_tabs.setDocumentMode(True)
        self.result_tabs.addTab(self.table, "Resultados")
        self.result_tabs.addTab(self.output, "DBMS_OUTPUT")
        self.result_tabs.setCornerWidget(clear_out, Qt.TopRightCorner)
        self.output_check.toggled.connect(lambda on: self.result_tabs.setTabVisible(1, on))
        self.last_output = 0

        bar = QHBoxLayout()
        bar.addWidget(self.run_btn)
        bar.addWidget(self.cancel_btn)
        bar.addWidget(self.format_btn)
        bar.addWidget(self.output_check)
        bar.addSpacing(12)
        bar.addWidget(self.status, 1)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.editor)
        splitter.addWidget(self.result_tabs)
        splitter.setSizes([300, 400])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
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

    def set_running(self, running, text=""):
        self.running = running
        self.run_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        if running:
            self.run_text = text
            self.run_started = time.perf_counter()
            self.spin_frame = 0
            self.set_status("")
            self._tick()
            self.spin_timer.start()
        else:
            self.spin_timer.stop()

    def _tick(self):
        frame = self.SPINNER[self.spin_frame % len(self.SPINNER)]
        self.spin_frame += 1
        secs = time.perf_counter() - self.run_started
        self.status.setText(f"{frame}  {self.run_text}  {secs:.1f} s")

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
        cur = self.editor.textCursor()
        if cur.hasSelection():
            sql = cur.selectedText().replace("\u2029", "\n")
        else:
            sql = statement_at_cursor(self.editor.toPlainText(), cur.position())
        serveroutput, sql = sqlplus_to_sql(clean_statement(sql))
        if serveroutput is not None:              # SET SERVEROUTPUT ON|OFF: la casilla DBMS_OUTPUT
            self.output_check.setChecked(serveroutput)
            if not strip_comments(sql):
                self.set_status(f"DBMS_OUTPUT {'activado' if serveroutput else 'desactivado'}")
                return
        if not strip_comments(sql):
            self.set_status("No hay ninguna sentencia donde está el cursor")
            return
        if not self.confirm(sql):
            return
        self.last_output = 0
        self.result_tabs.setTabText(1, "DBMS_OUTPUT")
        self.set_running(True, "Ejecutando…" if not self.conn_tab.db.busy
                         else "Esperando a que termine otra hoja de esta conexión…")
        self.conn_tab.run_statement(sql, self.emitter)

    def confirm(self, sql):
        kw = first_keyword(sql)
        body = strip_comments(sql).upper()
        if kw in ("UPDATE", "DELETE") and not re.search(r"\bWHERE\b", body):
            msg = f"Este {kw} no tiene WHERE: afectará todas las filas de la tabla.\n\n¿Ejecutar de todas formas?"
        elif kw in ("DROP", "TRUNCATE"):
            msg = f"{kw} no se puede deshacer con Rollback.\n\n¿Ejecutar?"
        elif kw in DDL and self.conn_tab.pending:
            msg = ("Hay cambios sin confirmar en esta conexión y las sentencias DDL "
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
            self.result_tabs.setCurrentWidget(self.table)
        if "headers" in res:
            self.model = self.table.show_rows(res["headers"], res["rows"])
            more = f" (solo las primeras {MAX_ROWS})" if res["truncated"] else ""
            self.set_status(f"{len(res['rows'])} filas{more}  ·  {res['elapsed']:.2f} s")
        else:
            self.set_status(f"{res['message']}  ·  {res['elapsed']:.2f} s")

    def on_error(self, msg):
        self.set_running(False)
        if self.last_output:
            self.result_tabs.setCurrentWidget(self.output)
        self.set_status(msg, error=True)
