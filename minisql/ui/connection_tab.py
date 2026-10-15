"""Pestaña de una conexión: explorador a la izquierda; hojas, visores de objetos y depuración a la derecha."""
import re
import threading
import time

import oracledb
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..db.catalog import kind_label, resolve_object
from ..db.session import SessionBusy, connection_lost
from ..sql.text import DDL, DML
from .completion import CompletionProvider
from .debug_panel import DebugController, DebugPanel
from .debugger import PlsqlDebugger
from .explorer import ObjectExplorer
from .object_viewer import ObjectViewer
from .sheet import SheetWidget
from .style import ERROR_COLOR, MUTED_COLOR, OK_COLOR, WARN_COLOR
from .workers import TaskRunner

DEBUG_KEYS = (("F5", "continue"), ("F10", "over"), ("F11", "into"), ("Shift+F11", "out"), ("Shift+F5", "stop"))


class ConnectionTab(QWidget):
    """Una conexión abierta. Organiza sus pestañas (hojas de SQL y visores de objetos), la
    transacción pendiente y el cierre. Lo que habla con Oracle está en `db` (OracleSession)."""
    pending_changed = Signal()
    transaction_ended = Signal(str)   # "Commit" o "Rollback": lo pendiente ya no está pendiente
    connection_changed = Signal(bool) # se desconectó, se perdió o se volvió a conectar
    _lost = Signal(str)               # desde un hilo: Oracle respondió que la conexión ya no existe
    CLOSE_WAIT_S = 10       # cuánto esperar a que Oracle atienda la cancelación al cerrar

    def __init__(self, db, name, dsn_label=None):
        super().__init__()
        self.db = db
        self.name = name
        self.dsn_label = dsn_label or getattr(db.conn, "dsn", "")
        self.pending = False
        self.reconnecting = False
        self.sheet_counter = 0
        self._lost.connect(self._on_lost)
        self.runner = TaskRunner(self)
        self.completion = CompletionProvider(self.meta_call, db.schema, self)
        self.debugger = PlsqlDebugger(db, self)

        self.sheets = QTabWidget()
        self.sheets.setTabsClosable(True)
        self.sheets.setMovable(True)
        self.sheets.setDocumentMode(True)
        self.sheets.tabCloseRequested.connect(self.close_sheet)
        self.sheets.tabBarDoubleClicked.connect(self.rename_sheet)
        plus = QToolButton()
        plus.setText("+ Hoja")
        plus.setToolTip("Nueva hoja (Ctrl+T)")
        plus.clicked.connect(lambda: self.add_sheet())
        self.sheets.setCornerWidget(plus, Qt.TopRightCorner)

        self.explorer = ObjectExplorer(self)
        self.debug_panel = DebugPanel(self.debugger)
        self.debug_panel.hide()
        self.debug = DebugController(self.debugger, self.debug_panel, self.open_object, self.set_pending, self)
        for seq, key in DEBUG_KEYS:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda k=key: self.debugger.active and self.debug_panel.do(k))

        right = QSplitter(Qt.Vertical)
        right.addWidget(self.sheets)
        right.addWidget(self.debug_panel)
        right.setStretchFactor(0, 1)
        right.setSizes([500, 220])
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.explorer)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 900])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(self._transaction_bar())
        layout.addWidget(splitter, 1)
        self.add_sheet()

    def _transaction_bar(self):
        commit_btn = self.commit_btn = QPushButton("✔ Commit")
        rollback_btn = self.rollback_btn = QPushButton("↶ Rollback")
        commit_btn.clicked.connect(self.commit)
        rollback_btn.clicked.connect(self.rollback)
        self.disconnect_btn = QPushButton("⏏ Desconectar")
        self.disconnect_btn.setToolTip("Cierra la sesión de Oracle; las hojas quedan abiertas")
        self.disconnect_btn.clicked.connect(self.disconnect)
        self.reconnect_btn = QPushButton("⟳ Reconectar")
        self.reconnect_btn.setToolTip("Abre una sesión nueva de Oracle con el mismo usuario (sin pedir la contraseña)")
        self.reconnect_btn.clicked.connect(lambda: self.reconnect())
        self.conn_state = QLabel("")
        self.conn_state.setStyleSheet(f"color: {ERROR_COLOR}; font-weight: bold;")
        self.pending_label = QLabel("")
        self.pending_label.setStyleSheet(f"color: {WARN_COLOR}; font-weight: bold;")
        conn = self.db.conn
        info = QLabel(f"{conn.username}@{self.dsn_label}   (Oracle {conn.version})")
        info.setStyleSheet(f"color: {MUTED_COLOR};")
        bar = QHBoxLayout()
        bar.addWidget(commit_btn)
        bar.addWidget(rollback_btn)
        bar.addSpacing(12)
        bar.addWidget(self.disconnect_btn)
        bar.addWidget(self.reconnect_btn)
        bar.addSpacing(12)
        bar.addWidget(self.conn_state)
        bar.addWidget(self.pending_label)
        bar.addStretch()
        bar.addWidget(info)
        return bar

    # --- hojas
    def add_sheet(self, title=None, text=""):
        if title is None:
            self.sheet_counter += 1
            title = f"Hoja{self.sheet_counter}"
        sheet = SheetWidget(self)
        if text:
            sheet.editor.setPlainText(text)
            sheet.editor.document().setModified(False)
        idx = self.sheets.addTab(sheet, title)
        self.sheets.setCurrentIndex(idx)
        sheet.editor.setFocus()
        return sheet

    def rename_sheet(self, i):
        if i < 0 or not isinstance(self.sheets.widget(i), SheetWidget):
            return
        text, ok = QInputDialog.getText(self, "Renombrar hoja", "Nombre:", text=self.sheets.tabText(i))
        if ok and text.strip():
            self.sheets.setTabText(i, text.strip())

    def current_sheet(self):
        """La hoja de SQL actual (si está viendo un objeto, la última hoja de SQL)."""
        w = self.sheets.currentWidget()
        if isinstance(w, SheetWidget):
            return w
        for i in range(self.sheets.count() - 1, -1, -1):
            if isinstance(self.sheets.widget(i), SheetWidget):
                return self.sheets.widget(i)
        return None

    def close_sheet(self, i):
        sheet = self.sheets.widget(i)
        if sheet.running and not self._cancel_for_sheet(i, sheet):
            return
        i = self.sheets.indexOf(sheet)            # la posición pudo cambiar mientras esperábamos
        if isinstance(sheet, ObjectViewer):
            if sheet.has_unsaved() and not self._confirm(
                    "Cerrar", f"{sheet.name} tiene cambios sin compilar. ¿Cerrar de todas formas?"):
                return
            sheet.forget_breakpoints()
        elif sheet.results.has_unsaved_changes() and not self._confirm(
                "Cerrar hoja", f"{self.sheets.tabText(i)} tiene cambios sin guardar en los resultados. "
                "¿Cerrar y descartarlos?"):
            return
        elif sheet.editor.document().isModified() and sheet.editor.toPlainText().strip():
            if not self._confirm("Cerrar hoja", f"¿Cerrar {self.sheets.tabText(i)}? Se perderá el SQL que contiene."):
                return
        self.sheets.removeTab(i)
        panel = sheet.data_panel if isinstance(sheet, ObjectViewer) else sheet.results
        if panel is not None:
            panel.dispose()                      # cierra el cursor que quedó abierto para paginar
        sheet.deleteLater()
        if self.current_sheet() is None:
            self.add_sheet()                 # siempre queda al menos una hoja de SQL

    def _cancel_for_sheet(self, i, sheet):
        if not self._ask("En ejecución", f"{self.sheets.tabText(i)} tiene una consulta en curso.\n\n"
                         "¿Cancelarla y cerrar la hoja?", "Cancelar y cerrar", "Seguir esperando"):
            return False
        self.db.cancel()
        if not self._wait_until_free(self.CLOSE_WAIT_S) or sheet.running:
            QMessageBox.information(self, "En ejecución",
                                    "La consulta todavía no responde a la cancelación. Intenta de nuevo en un momento.")
            return False
        return True

    # --- visores de objetos
    def open_object(self, owner, name, otype):
        key = (owner, name, otype)
        for i in range(self.sheets.count()):
            w = self.sheets.widget(i)
            if isinstance(w, ObjectViewer) and w.key == key:
                self.sheets.setCurrentIndex(i)
                return w
        viewer = ObjectViewer(self, owner, name, otype)
        idx = self.sheets.addTab(viewer, f"◆ {name}")
        self.sheets.setTabToolTip(idx, f"{owner}.{name} ({kind_label(otype)})")
        self.sheets.setCurrentIndex(idx)
        return viewer

    def open_object_by_name(self, owner, name, report):
        """F4: busca el objeto (siguiendo sinónimos) y lo abre. report(texto) muestra el estado."""
        report(f"Buscando {name}…")
        me = self.db.schema

        def ok(found):
            if not found:
                report(f"No encontré el objeto {f'{owner}.' if owner else ''}{name}")
                return
            report("")
            self.open_object(*found)

        self.meta_call(lambda cur: resolve_object(cur, me, owner, name), ok, lambda m: report(m.splitlines()[0]))

    # --- trabajo en hilos
    def meta_call(self, fn, on_ok, on_err):
        """Ejecuta fn(cursor) en la sesión de metadatos, en un hilo; el resultado vuelve a la interfaz."""
        self.runner.run(self._watch(lambda: self.db.with_meta_cursor(fn)), on_ok, on_err)

    def run_statement(self, sql, emitter, with_rowid=False):
        """Ejecuta sql en la sesión principal, en un hilo. Avisa por emitter: output, done o failed.
        with_rowid: trae también el ROWID de cada fila, para poder editar los resultados."""
        run = self.db.execute_with_rowid if with_rowid else self.db.execute
        self._run_in_thread(lambda: run(sql, emitter.output.emit), emitter)

    def run_script(self, statements, emitter):
        """Ejecuta varias sentencias en orden en la sesión principal, en un hilo (ver OracleSession.execute_script)."""
        self._run_in_thread(lambda: self.db.execute_script(statements, emitter.output.emit), emitter)

    def _watch(self, fn):
        """Envuelve fn (corre en un hilo): si Oracle dice que la conexión ya no existe, avisa a la interfaz."""
        def run():
            try:
                return fn()
            except Exception as e:
                if connection_lost(e) and self.db.connected:
                    self.db.connected = False
                    self._lost.emit(str(e).splitlines()[0])
                raise
        return run

    def _run_in_thread(self, fn, emitter):
        fn = self._watch(fn)

        def work():
            try:
                res = fn()
            except Exception as e:
                emitter.failed.emit(str(e))
            else:
                emitter.done.emit(res)
        threading.Thread(target=work, daemon=True).start()

    def start_debug(self, block, compile_targets=(), target=None):
        if not self.connected:
            QMessageBox.information(self, "Sin conexión", f"{self.name} está desconectada: usa ⟳ Reconectar.")
            return
        self.debug.start(block, compile_targets, target)

    # --- transacción
    def after_statement(self, res):
        kw = res.get("kw", "")
        if kw in DDL:
            self.completion.clear()   # pudieron cambiar tablas o columnas
        if kw == "ROLLBACK":
            self.transaction_ended.emit("Rollback")
        elif kw == "COMMIT" or kw in DDL:            # el DDL hace COMMIT de lo pendiente
            self.transaction_ended.emit("Commit")
        if res.get("txn") is not None:
            self.set_pending(bool(res["txn"]))
        elif kw in DML or kw in ("BEGIN", "DECLARE", "CALL"):
            self.set_pending(True)
        elif kw in DDL or kw in ("COMMIT", "ROLLBACK"):
            self.set_pending(False)

    def refresh_pending(self):
        """Después de un error: las sentencias anteriores de un script pudieron dejar cambios sin confirmar."""
        if self.db.transaction_in_progress:
            self.set_pending(True)

    def set_pending(self, value):
        self.pending = value
        self.pending_label.setText("● Cambios sin confirmar" if value else "")
        self.pending_changed.emit()

    def commit(self):
        return self._end_transaction(self.db.commit, "Commit")

    def rollback(self):
        return self._end_transaction(self.db.rollback, "Rollback")

    def _end_transaction(self, action, label):
        try:
            action()
        except SessionBusy as e:
            QMessageBox.information(self, "Ocupado", str(e))
            return False
        except oracledb.Error as e:
            QMessageBox.critical(self, f"Error en {label}", str(e))
            return False
        self.set_pending(False)
        self.transaction_ended.emit(label)
        return True

    # --- guardián de sesión: lo que se guarda para no perder nada
    def snapshot(self):
        tabs = []
        for i in range(self.sheets.count()):
            w = self.sheets.widget(i)
            if isinstance(w, SheetWidget):
                tab = {"kind": "sheet", "title": self.sheets.tabText(i),
                       "text": w.editor.toPlainText(), "cursor": w.editor.textCursor().position()}
            elif isinstance(w, ObjectViewer):
                tab = {"kind": "object", "owner": w.owner, "name": w.name, "type": w.otype,
                       "code": w.unsaved_code()}
            else:
                continue
            if i == self.sheets.currentIndex():
                tab["active"] = True                 # la hoja que se estaba usando: se vuelve a elegir al abrir
            tabs.append(tab)
        return {"name": self.name, "user": self.db.user, "dsn": self.dsn_label, "tabs": tabs}

    def restore(self, state):
        """Vuelve a abrir las hojas y objetos de un snapshot()."""
        tabs = state.get("tabs") or []
        if not tabs:
            return
        blank = [self.sheets.widget(i) for i in range(self.sheets.count())
                 if isinstance(self.sheets.widget(i), SheetWidget)
                 and not self.sheets.widget(i).editor.toPlainText().strip()]
        restored = []
        for t in tabs:
            if t.get("kind") == "sheet":
                restored.append(self._restore_sheet(t))
            elif t.get("kind") == "object":
                viewer = self.open_object(t["owner"], t["name"], t["type"])
                if t.get("code"):
                    viewer.restore_code(t["code"])
                restored.append(viewer)
        for w in blank:                          # la hoja vacía inicial ya no hace falta
            if self.sheets.count() > 1:
                self.sheets.removeTab(self.sheets.indexOf(w))
                w.deleteLater()
        active = next((i for i, t in enumerate(tabs) if t.get("active")), None)
        if active is None and isinstance(state.get("current"), int):
            active = state["current"]            # formato anterior: la posición de la hoja activa
        if active is not None and 0 <= active < len(restored):
            self.sheets.setCurrentWidget(restored[active])

    def _restore_sheet(self, t):
        text = t.get("text", "")
        sheet = self.add_sheet(title=t.get("title") or None, text=text)
        sheet.editor.document().setModified(bool(text.strip()))   # pedirá confirmar antes de cerrarla
        cur = sheet.editor.textCursor()
        cur.setPosition(min(t.get("cursor", 0), len(text)))
        sheet.editor.setTextCursor(cur)
        QTimer.singleShot(0, sheet.editor.ensureCursorVisible)   # el cursor recuperado, siempre a la vista
        m = re.fullmatch(r"Hoja(\d+)", t.get("title") or "")
        if m:
            self.sheet_counter = max(self.sheet_counter, int(m.group(1)))
        return sheet

    # --- cierre
    def _ask(self, title, text, yes_text, no_text):
        """Pregunta con botones claros. Devuelve True si eligió yes_text."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(text)
        yes = box.addButton(yes_text, QMessageBox.AcceptRole)
        box.addButton(no_text, QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() == yes

    def _confirm(self, title, text):
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    def _wait_until_free(self, timeout):
        """Espera (sin congelar la ventana) a que termine lo que corre en la sesión principal."""
        end = time.perf_counter() + timeout
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            while time.perf_counter() < end:
                QApplication.processEvents()
                if not self.debugger.active and self.db.wait_free(0.05):
                    QApplication.processEvents()      # que la hoja reciba el aviso de cancelación
                    return True
            return False
        finally:
            QApplication.restoreOverrideCursor()

    def close_connection(self):
        """Devuelve True si se pudo cerrar. Si hay algo corriendo, ofrece cancelarlo; si hay cambios
        sin confirmar, pregunta Commit o Rollback."""
        if not self._release_session("cerrar"):
            return False
        self.db.close()
        return True

    def _release_session(self, verb):
        """Antes de cerrar, desconectar o reconectar (verb): cambios de la grilla sin guardar, algo
        corriendo y cambios sin confirmar. True si se puede seguir."""
        unsaved = [self.sheets.tabText(i) for i in range(self.sheets.count())
                   if hasattr(self.sheets.widget(i), "results") and self.sheets.widget(i).results.has_unsaved_changes()]
        if unsaved and not self._confirm(
                "Cambios sin guardar", f"{self.name}: hay cambios sin guardar en los resultados de "
                f"{', '.join(unsaved)}. ¿{verb.capitalize()} y descartarlos?"):
            return False
        if not self.db.connected:
            return True                          # ya no hay sesión: nada corriendo ni pendiente que salvar
        if self.debugger.active or self.db.busy:
            closed = self._cancel_running_work(verb)
            if closed is not None:
                return closed
        return not self.pending or self._resolve_pending(verb)

    def _cancel_running_work(self, verb="cerrar"):
        """None si quedó libre; True si se cerró a la fuerza; False si el usuario prefirió esperar."""
        what = "una depuración en curso" if self.debugger.active else "una consulta en ejecución"
        if not self._ask("En ejecución", f"{self.name} tiene {what}.\n\n¿Cancelarla y {verb}?",
                         f"Cancelar y {verb}", "Seguir esperando"):
            return False
        if self.debugger.active:
            self.debugger.stop()
        else:
            self.db.cancel()
        if self._wait_until_free(self.CLOSE_WAIT_S):
            return None
        if not self._ask("Sin respuesta",
                         f"{self.name} no respondió a la cancelación.\n\n¿{verb.capitalize()} de todas formas? "
                         "Oracle deshará (Rollback) los cambios sin confirmar de esta conexión.",
                         f"{verb.capitalize()} de todas formas", "Seguir esperando"):
            return False
        self.db.close()
        return True

    def _resolve_pending(self, verb="cerrar"):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Cambios sin confirmar")
        box.setText(f"La conexión {self.name} tiene cambios sin confirmar.")
        commit_b = box.addButton(f"Commit y {verb}", QMessageBox.AcceptRole)
        rollback_b = box.addButton(f"Rollback y {verb}", QMessageBox.DestructiveRole)
        box.addButton("Cancelar", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() == commit_b:
            return self.commit()
        if box.clickedButton() == rollback_b:
            return self.rollback()
        return False

    # --- desconectar y reconectar
    @property
    def connected(self):
        return self.db.connected

    def disconnect(self):
        """Cierra la sesión de Oracle; la pestaña, las hojas y los visores quedan como están."""
        if not self.connected or self.reconnecting or not self._release_session("desconectar"):
            return False
        self._forget_streams()
        self.db.disconnect()
        self.set_pending(False)
        self._set_connected(False, "● Desconectado")
        return True

    def reconnect(self, then=None):
        """Abre una sesión nueva con el mismo usuario (sin pedir la contraseña: está en memoria).
        then: qué hacer al conectarse (p. ej. ejecutar la consulta que se pidió sin conexión)."""
        if self.reconnecting or (self.connected and not self._release_session("reconectar")):
            return
        self._forget_streams()
        self.reconnecting = True
        self._update_connection_widgets()
        self.conn_state.setStyleSheet(f"color: {MUTED_COLOR}; font-weight: bold;")
        self.conn_state.setText("⟳ Reconectando…")

        def ok(_):
            self.reconnecting = False
            self.set_pending(False)
            self.completion.clear()                  # metadatos nuevos
            self._set_connected(True)
            self.explorer.load_schemas()
            if then:
                then()

        def failed(msg):
            self.reconnecting = False
            self._set_connected(False, "● Sin conexión")
            QMessageBox.warning(self, "Reconectar", f"No se pudo reconectar {self.name}:\n\n{msg}")

        self.runner.run(self.db.reconnect, ok, failed)

    def _on_lost(self, msg):
        lost_changes = self.pending
        self._forget_streams()
        self.set_pending(False)
        self._set_connected(False, "● Conexión perdida")
        self.conn_state.setToolTip(msg)
        detail = ("\n\nLos cambios sin confirmar se perdieron (Oracle los deshizo al cortarse la sesión)."
                  if lost_changes else "")
        QMessageBox.warning(self, "Conexión perdida",
                            f"Se perdió la conexión {self.name} con Oracle:\n{msg}{detail}\n\n"
                            "Tus hojas siguen aquí. Usa ⟳ Reconectar para seguir trabajando.")

    def _set_connected(self, connected, why=""):
        if connected:
            self.conn_state.setText("")
            self.conn_state.setToolTip("")
        else:
            self.conn_state.setStyleSheet(f"color: {ERROR_COLOR}; font-weight: bold;")
            self.conn_state.setText(why)
        self._update_connection_widgets()
        self.connection_changed.emit(connected)
        self.pending_changed.emit()                  # el título de la pestaña muestra el estado

    def _update_connection_widgets(self):
        on = self.connected and not self.reconnecting
        for b in (self.commit_btn, self.rollback_btn, self.disconnect_btn):
            b.setEnabled(on)
        self.reconnect_btn.setEnabled(not self.reconnecting)
        self.reconnect_btn.setStyleSheet("" if self.connected else f"color: {OK_COLOR}; font-weight: bold;")

    def _forget_streams(self):
        """Los cursores abiertos para paginar mueren con la sesión."""
        for i in range(self.sheets.count()):
            w = self.sheets.widget(i)
            panel = w.data_panel if isinstance(w, ObjectViewer) else getattr(w, "results", None)
            if panel is not None:
                panel.forget_stream()
