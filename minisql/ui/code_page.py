"""Pestaña "Código" del visor: editar, compilar, ver errores, poner breakpoints y depurar."""
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..db.catalog import fetch_arguments
from ..db.source import compile_code, fetch_errors
from ..sql.plsql import debug_template, editor_line, map_errors, unit_at_line
from ..sql.text import IDENT, norm_ident
from .debug_panel import DebugStartDialog
from .editors import CodeEditor
from .find_bar import with_find_bar
from .results import ResultView
from .style import ERROR_COLOR, MUTED_COLOR, OK_COLOR, WARN_COLOR

TRIGGER_TEMPLATE = ("-- Escribe aquí lo que hace que se ejecute el código\n"
                    "-- (por ejemplo, un INSERT o UPDATE que dispare el trigger)\nBEGIN\n  NULL;\nEND;")


class CodePage(QWidget):
    """Código PL/SQL de un objeto. El texto llega con set_code() cuando el visor lo carga."""

    def __init__(self, conn_tab, owner, name, otype, status):
        super().__init__()
        self.conn_tab = conn_tab
        self.owner, self.name, self.otype = owner, name, otype
        self.status = status                   # StatusLabel del visor
        self.loaded = False
        self.recovered_code = None             # código sin compilar de la sesión anterior
        self.pending_exec = None               # línea del depurador que llegó antes que el código

        self.editor = CodeEditor(conn_tab.completion)
        compile_btn = QPushButton("✔ Compilar  (Ctrl+S)")
        compile_btn.setToolTip("Crea o reemplaza el objeto en la base con este código")
        debug_btn = QPushButton("🐞 Depurar…")
        debug_btn.setToolTip("Ejecuta el código paso a paso con DBMS_DEBUG")
        hint = QLabel("Clic en el margen o F9: punto de interrupción")
        hint.setStyleSheet(f"color: {MUTED_COLOR};")
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(compile_btn)
        bar.addWidget(debug_btn)
        bar.addSpacing(12)
        bar.addWidget(hint, 1)

        self.errors = ResultView()
        self.errors.hide()
        self.errors.doubleClicked.connect(
            lambda idx: self.editor.go_to_line(*self.errors.model().rows[idx.row()][:2]))
        split = QSplitter(Qt.Vertical)
        editor_box = with_find_bar(self.editor)         # Ctrl+F buscar, Ctrl+H reemplazar
        self.find_bar = editor_box.find_bar
        split.addWidget(editor_box)
        split.addWidget(self.errors)
        split.setSizes([500, 120])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(split, 1)

        compile_btn.clicked.connect(self.compile)
        debug_btn.clicked.connect(self.start_debug)
        sc = QShortcut(QKeySequence("Ctrl+S"), self)
        sc.setContext(Qt.WidgetWithChildrenShortcut)
        sc.activated.connect(self.compile)
        self.editor.breakpoint_toggled.connect(self._breakpoint_toggled)

    # --- contenido
    def set_code(self, code):
        self.editor.setPlainText(code)
        self.editor.document().setModified(False)
        self.loaded = True
        self.conn_tab.meta_call(lambda cur: fetch_errors(cur, self.owner, self.name),
                                self.show_errors, lambda _m: None)
        if self.recovered_code:
            self._apply_recovered(self.recovered_code)
            self.recovered_code = None
        if self.pending_exec:
            self.show_exec_line(*self.pending_exec)

    def has_unsaved(self):
        return self.editor.document().isModified()

    def restore_code(self, code):
        """Código sin compilar recuperado de la sesión anterior: reemplaza al de la base cuando cargue."""
        if self.loaded:
            self._apply_recovered(code)
        else:
            self.recovered_code = code

    def _apply_recovered(self, code):
        self.editor.setPlainText(code)
        self.editor.document().setModified(True)
        self.status.show_message("Cambios sin compilar recuperados de la sesión anterior", WARN_COLOR)

    # --- compilar
    def show_errors(self, errors):
        rows = map_errors(self.editor.toPlainText(), errors, self.owner)
        self.errors.show_rows(["Línea", "Columna", "Error"], rows)
        self.errors.setVisible(bool(rows))
        return rows

    def compile(self):
        if not self.loaded:
            return
        tab = self.conn_tab
        if not tab.db.own_meta and tab.pending:
            r = QMessageBox.question(self, "Compilar",
                                     "Hay cambios sin confirmar en esta conexión y compilar hace COMMIT "
                                     "automático de ellos.\n\n¿Continuar?")
            if r != QMessageBox.Yes:
                return
        code, me = self.editor.toPlainText(), tab.db.schema
        self.status.show_message("Compilando…")
        tab.meta_call(lambda cur: compile_code(cur, code, self.owner, me), self._compiled, self.status.error)

    def _compiled(self, errors):
        tab = self.conn_tab
        self.editor.document().setModified(False)
        tab.completion.clear()
        rows = self.show_errors(errors)
        if rows:
            self.status.show_message(f"Compilado con {len(rows)} error(es)", ERROR_COLOR)
            self.editor.go_to_line(rows[0][0], rows[0][1])
        else:
            self.status.show_message("Compilado sin errores", OK_COLOR)
        if not tab.db.own_meta:
            tab.set_pending(False)           # el DDL hizo COMMIT en la sesión principal

    # --- depuración
    def _breakpoint_toggled(self, line, added):
        key = unit_at_line(self.editor.toPlainText(), line, self.owner)
        if key:
            self.conn_tab.debugger.breakpoint_changed(key, added)

    def forget_breakpoints(self):
        for line in list(self.editor.breakpoints):
            self.editor.toggle_breakpoint(line)

    def show_exec_line(self, utype, line, values=()):
        """Marca dónde está detenido el depurador y guarda los valores de las variables para verlos al
        pasar el mouse (o lo recuerda si el código aún no llega)."""
        if not self.loaded:
            self.pending_exec = (utype, line, values)
            return
        self.pending_exec = None
        self.editor.set_debug_values(values)
        self.editor.set_exec_line(editor_line(self.editor.toPlainText(), utype, self.name, line, self.owner))

    def clear_exec_line(self):
        self.editor.set_exec_line(None)
        self.editor.set_debug_values([])
        self.pending_exec = None

    def start_debug(self):
        tab = self.conn_tab
        if tab.debugger.active:
            QMessageBox.information(self, "Depuración", "Ya hay una depuración en curso en esta conexión.")
            return
        if self.has_unsaved():
            QMessageBox.information(self, "Depuración", "Compila los cambios (Ctrl+S) antes de depurar.")
            return
        owner, name, otype = self.owner, self.name, self.otype
        sub = self._subprogram_at_cursor() if otype == "PACKAGE" else None

        def fn(cur):
            return fetch_arguments(cur, owner, name, sub) if otype in ("PROCEDURE", "FUNCTION", "PACKAGE") else None

        def ok(found):
            if otype == "PACKAGE" and found:
                block = debug_template(owner, name, found[0], found[1])
            elif otype in ("PROCEDURE", "FUNCTION"):
                block = debug_template(owner, None, name, found[1] if found else [])
            else:
                block = TRIGGER_TEMPLATE
            dlg = DebugStartDialog(self, f"{owner}.{name}", block, True)
            if dlg.exec() != QDialog.Accepted:
                return
            targets = [(otype, owner, name)] if dlg.compile_debug.isChecked() else []
            unit = {"PACKAGE": "PACKAGE BODY", "TYPE": "TYPE BODY"}.get(otype, otype)
            tab.start_debug(dlg.block(), targets, target=(unit, owner, name))

        tab.meta_call(fn, ok, self.status.error)

    def _subprogram_at_cursor(self):
        """En un paquete, el último PROCEDURE/FUNCTION declarado antes del cursor."""
        code = self.editor.toPlainText()[:self.editor.textCursor().position()]
        found = re.findall(r"\b(?:PROCEDURE|FUNCTION)\s+(" + IDENT + ")", code, re.I)
        return norm_ident(found[-1]) if found else None
