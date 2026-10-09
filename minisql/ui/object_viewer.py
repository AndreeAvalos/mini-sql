"""Pestaña de un objeto de la base: columnas, datos, código, índices, etc."""
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget

from ..config import MAX_ROWS
from ..db.catalog import WITH_DATA, kind_label
from ..db.object_tabs import Text, fetch_object_tab
from ..sql.text import q
from .code_page import CodePage
from .editors import CodeEditor
from .find_bar import with_find_bar
from .results import ResultView
from .style import StatusLabel
from .workers import Emitter

# Pestañas de cada tipo de objeto. Cada nombre necesita un cargador en db/object_tabs.py,
# salvo "Datos" (consulta en la sesión principal) y "Código" (CodePage).
OBJECT_TABS = {
    "TABLE": ["Columnas", "Datos", "Índices", "Restricciones", "Detalles", "DDL"],
    "VIEW": ["Columnas", "Datos", "Detalles", "DDL"],
    "MATERIALIZED VIEW": ["Columnas", "Datos", "Índices", "Detalles", "DDL"],
    "PROCEDURE": ["Código", "Argumentos", "Detalles"],
    "FUNCTION": ["Código", "Argumentos", "Detalles"],
    "PACKAGE": ["Código", "Argumentos", "Detalles"],
    "TYPE": ["Código", "Detalles"],
    "TRIGGER": ["Código", "Detalles"],
    "SEQUENCE": ["Detalles", "DDL"],
    "SYNONYM": ["Detalles", "DDL"],
    "INDEX": ["Columnas", "Detalles", "DDL"],
}


class ObjectViewer(QWidget):
    """Muestra un objeto en pestañas; cada una se carga la primera vez que se ve."""

    def __init__(self, conn_tab, owner, name, otype):
        super().__init__()
        self.conn_tab = conn_tab
        self.owner, self.name, self.otype = owner, name, otype
        self.key = (owner, name, otype)
        self.running = False                 # la pestaña Datos está consultando en la sesión principal
        self.full = f"{q(owner)}.{q(name)}"

        title = QLabel(f"{kind_label(otype).capitalize()}  <b>{owner}.{name}</b>")
        self.status = StatusLabel()
        refresh = QPushButton("⟳ Refrescar")
        refresh.setToolTip("Volver a cargar la pestaña actual")
        to_sheet = QPushButton("Abrir en hoja")
        to_sheet.setToolTip("Abre el SELECT o el código en una hoja nueva para editarlo")
        bar = QHBoxLayout()
        bar.addWidget(title)
        bar.addSpacing(12)
        bar.addWidget(self.status, 1)
        bar.addWidget(refresh)
        bar.addWidget(to_sheet)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.pages = {}                      # nombre de pestaña -> ResultView o editor
        self.code = None                     # CodePage si el objeto tiene código
        for label in OBJECT_TABS.get(otype, ["Detalles", "DDL"]):
            if label == "Código":
                self.code = CodePage(conn_tab, owner, name, otype, self.status)
                self.pages[label] = self.code.editor
                self.tabs.addTab(self.code, label)
                continue
            if label == "DDL":
                page = CodeEditor()
                page.setReadOnly(True)
                self.tabs.addTab(with_find_bar(page), label)   # Ctrl+F para buscar en el DDL
            else:
                page = ResultView()
                self.tabs.addTab(page, label)
            self.pages[label] = page
        self.loaded = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(bar)
        layout.addWidget(self.tabs, 1)

        self.emitter = Emitter()
        self.emitter.done.connect(self._data_done)
        self.emitter.failed.connect(self._data_failed)
        self.tabs.currentChanged.connect(lambda _i: self.load_current())
        refresh.clicked.connect(lambda: self.load_current(force=True))
        to_sheet.clicked.connect(self.open_in_sheet)
        self.load_current()

    # --- lo que usan la conexión, el depurador y el guardián de sesión
    @property
    def errors(self):
        return self.code.errors if self.code else None

    def has_unsaved(self):
        return bool(self.code and self.code.has_unsaved())

    def unsaved_code(self):
        return self.code.editor.toPlainText() if self.has_unsaved() else None

    def restore_code(self, code):
        if self.code:
            self.code.restore_code(code)

    def compile(self):
        if self.code:
            self.code.compile()

    def forget_breakpoints(self):
        if self.code:
            self.code.forget_breakpoints()

    def show_exec_line(self, utype, line, values=()):
        if self.code:
            self.tabs.setCurrentWidget(self.code)
            self.code.show_exec_line(utype, line, values)

    def clear_exec_line(self):
        if self.code:
            self.code.clear_exec_line()

    # --- carga de pestañas
    def show_tab(self, label):
        """Muestra la pestaña interna con ese nombre (Columnas, Datos, DDL…)."""
        labels = [self.tabs.tabText(i) for i in range(self.tabs.count())]
        self.tabs.setCurrentIndex(labels.index(label))

    def current_label(self):
        return self.tabs.tabText(self.tabs.currentIndex())

    def load_current(self, force=False):
        label = self.current_label()
        if label in self.loaded and not force:
            return
        if force and label == "Código" and self.has_unsaved():
            r = QMessageBox.question(self, "Refrescar", "Se perderán los cambios sin compilar. ¿Continuar?")
            if r != QMessageBox.Yes:
                return
        self.loaded.add(label)
        if label == "Datos":
            self.load_data()
            return
        self.status.show_message("Cargando…")

        def ok(result):
            self.status.show_message("")
            if label == "Código":
                self.code.set_code(result.text)
            elif isinstance(result, Text):
                self.pages[label].setPlainText(result.text)
            else:
                self.pages[label].show_rows(result.headers, result.rows)

        def err(msg):
            self.loaded.discard(label)       # al volver a la pestaña se reintenta
            self.status.error(msg)

        self.conn_tab.meta_call(lambda cur: fetch_object_tab(cur, self.owner, self.name, self.otype, label),
                                ok, err)

    # --- datos: en la sesión principal, para ver también los cambios sin confirmar
    def load_data(self):
        if self.running:
            return
        self.running = True
        self.status.show_message("Cargando datos…" if not self.conn_tab.db.busy
                                 else "Esperando a que termine la consulta en curso…")
        self.conn_tab.run_statement(f"SELECT * FROM {self.full}", self.emitter)

    def _data_done(self, res):
        self.running = False
        self.pages["Datos"].show_rows(res["headers"], res["rows"])
        more = f" (solo las primeras {MAX_ROWS})" if res["truncated"] else ""
        self.status.show_message(f"{len(res['rows'])} filas{more}  ·  {res['elapsed']:.2f} s")

    def _data_failed(self, msg):
        self.running = False
        self.loaded.discard("Datos")
        self.status.error(msg)

    def open_in_sheet(self):
        label = self.current_label()
        page = self.pages.get(label)
        if label in ("Código", "DDL") and page.toPlainText():
            text = page.toPlainText()
        elif self.otype in WITH_DATA:
            text = f"SELECT *\nFROM {self.full}"
        else:
            source = self.pages.get("Código") or self.pages.get("DDL")
            text = source.toPlainText() if source else ""
        self.conn_tab.add_sheet(title=self.name, text=text)
