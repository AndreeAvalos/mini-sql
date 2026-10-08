"""Árbol de esquemas y objetos."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..db.catalog import (
    WITH_COLUMNS,
    WITH_DATA,
    format_type,
    list_columns,
    list_objects,
    list_schemas,
    list_subprograms,
)
from ..db.source import fetch_ddl
from ..sql.text import q
from .style import ERROR_COLOR, MUTED_COLOR

CATEGORIES = [
    ("Tablas", "TABLE"),
    ("Vistas", "VIEW"),
    ("Vistas materializadas", "MATERIALIZED VIEW"),
    ("Procedimientos", "PROCEDURE"),
    ("Funciones", "FUNCTION"),
    ("Paquetes", "PACKAGE"),
    ("Tipos", "TYPE"),
    ("Triggers", "TRIGGER"),
    ("Secuencias", "SEQUENCE"),
    ("Sinónimos", "SYNONYM"),
    ("Índices", "INDEX"),
]


INFO = Qt.UserRole


class ObjectExplorer(QWidget):
    """Árbol esquemas > categorías > objetos > columnas/subprogramas, que se carga al expandir.
    Doble clic abre el objeto; el menú contextual inserta nombres, consultas o el DDL."""

    def __init__(self, conn_tab):
        super().__init__()
        self.tab = conn_tab
        self.me = conn_tab.db.schema

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filtrar objetos por nombre…")
        self.filter.setClearButtonEnabled(True)
        self.hide_sys = QCheckBox("Ocultar esquemas de Oracle")
        self.hide_sys.setChecked(True)
        refresh = QToolButton()
        refresh.setText("⟳")
        refresh.setToolTip("Recargar esquemas")

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)

        top = QHBoxLayout()
        top.addWidget(self.hide_sys, 1)
        top.addWidget(refresh)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.filter)
        layout.addLayout(top)
        layout.addWidget(self.tree)

        self.tree.itemExpanded.connect(self.on_expand)
        self.tree.itemDoubleClicked.connect(self.on_double_click)
        self.tree.customContextMenuRequested.connect(self.on_menu)
        self.filter.textChanged.connect(self.apply_filter)
        self.hide_sys.toggled.connect(self.load_schemas)
        refresh.clicked.connect(self.load_schemas)
        refresh.clicked.connect(conn_tab.completion.clear)
        self.load_schemas()

    # --- utilidades de ítems
    @staticmethod
    def info(item):
        return dict(item.data(0, INFO) or {})

    @staticmethod
    def set_info(item, info):
        item.setData(0, INFO, info)

    def add_item(self, parent, text, info, expandable=False):
        it = QTreeWidgetItem([text])
        self.set_info(it, info)
        if expandable:
            it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        if parent is None:
            self.tree.addTopLevelItem(it)
        else:
            parent.addChild(it)
        return it

    def start_loading(self, item):
        info = self.info(item)
        info["loaded"] = True
        self.set_info(item, info)
        item.takeChildren()
        ph = QTreeWidgetItem(["Cargando…"])
        ph.setForeground(0, QColor(MUTED_COLOR))
        item.addChild(ph)

    def show_child_error(self, item, err):
        info = self.info(item)
        info["loaded"] = False          # al volver a expandir se reintenta
        self.set_info(item, info)
        item.takeChildren()
        e = QTreeWidgetItem([f"Error: {err.splitlines()[0]}"])
        e.setToolTip(0, err)
        e.setForeground(0, QColor(ERROR_COLOR))
        item.addChild(e)

    def qualified(self, owner, name):
        return q(name) if owner == self.me else f"{q(owner)}.{q(name)}"

    # --- esquemas
    def load_schemas(self):
        self.tree.clear()
        self.add_item(None, "Cargando esquemas…", {"kind": "placeholder"})
        hide = self.hide_sys.isChecked()

        def ok(names):
            self.tree.clear()
            ordered = [self.me] + [n for n in names if n != self.me]
            for name in ordered:
                it = self.add_item(None, name, {"kind": "schema", "owner": name}, expandable=True)
                if name == self.me:
                    f = it.font(0)
                    f.setBold(True)
                    it.setFont(0, f)
            if self.tree.topLevelItemCount():
                self.tree.topLevelItem(0).setExpanded(True)

        def err(msg):
            self.tree.clear()
            self.add_item(None, f"Error: {msg.splitlines()[0]}", {"kind": "placeholder"})

        self.tab.meta_call(lambda cur: list_schemas(cur, hide), ok, err)

    # --- expandir
    def on_expand(self, item):
        info = self.info(item)
        if info.get("loaded"):
            return
        kind = info.get("kind")
        if kind == "schema":
            info["loaded"] = True
            self.set_info(item, info)
            item.takeChildren()
            for label, otype in CATEGORIES:
                self.add_item(item, label, {"kind": "category", "owner": info["owner"],
                                            "type": otype, "label": label}, expandable=True)
        elif kind == "category":
            self.load_category(item, info)
        elif kind == "object" and info["type"] in WITH_COLUMNS:
            self.load_columns(item, info)
        elif kind == "object" and info["type"] == "PACKAGE":
            self.load_package(item, info)

    def load_category(self, item, info):
        self.start_loading(item)
        owner, otype = info["owner"], info["type"]

        def ok(rows):
            item.takeChildren()
            for name, status in rows:
                it = self.add_item(item, name, {"kind": "object", "owner": owner, "type": otype, "name": name},
                                   expandable=otype in WITH_COLUMNS or otype == "PACKAGE")
                if status != "VALID":
                    it.setForeground(0, QColor(ERROR_COLOR))
                    it.setToolTip(0, f"Estado: {status}")
            if not rows:
                e = QTreeWidgetItem(["(vacío)"])
                e.setForeground(0, QColor(MUTED_COLOR))
                item.addChild(e)
            info2 = self.info(item)
            info2["count"] = len(rows)
            self.set_info(item, info2)
            self.filter_category(item, self.filter.text().strip().upper())

        self.tab.meta_call(lambda cur: list_objects(cur, owner, otype), ok,
                           lambda m: self.show_child_error(item, m))

    def load_columns(self, item, info):
        self.start_loading(item)
        owner, name = info["owner"], info["name"]

        def ok(rows):
            item.takeChildren()
            for col, dtype, length, prec, scale, clen, nullable, pk in rows:
                text = f"{'🔑 ' if pk else ''}{col}   {format_type(dtype, length, prec, scale, clen)}"
                if nullable == "N":
                    text += "  NOT NULL"
                it = self.add_item(item, text, {"kind": "column", "owner": owner, "name": col})
                it.setForeground(0, QColor("#555555") if not pk else QColor("#8e6c00"))

        self.tab.meta_call(lambda cur: list_columns(cur, owner, name), ok,
                           lambda m: self.show_child_error(item, m))

    def load_package(self, item, info):
        self.start_loading(item)
        owner, name = info["owner"], info["name"]

        def ok(names):
            item.takeChildren()
            for sub in names:
                self.add_item(item, sub, {"kind": "subprogram", "owner": owner, "package": name, "name": sub})

        self.tab.meta_call(lambda cur: list_subprograms(cur, owner, name), ok,
                           lambda m: self.show_child_error(item, m))

    # --- filtro
    def apply_filter(self):
        text = self.filter.text().strip().upper()
        for i in range(self.tree.topLevelItemCount()):
            schema = self.tree.topLevelItem(i)
            for j in range(schema.childCount()):
                self.filter_category(schema.child(j), text)

    def filter_category(self, cat, text):
        info = self.info(cat)
        if info.get("kind") != "category" or "count" not in info:
            return
        shown = 0
        for k in range(cat.childCount()):
            ch = cat.child(k)
            ci = self.info(ch)
            if ci.get("kind") != "object":
                continue
            visible = not text or text in ci["name"]
            ch.setHidden(not visible)
            shown += visible
        total = info["count"]
        cat.setText(0, f"{info['label']} ({shown}/{total})" if text else f"{info['label']} ({total})")

    # --- acciones
    def insert_text(self, text):
        sheet = self.tab.current_sheet()
        if sheet:
            self.tab.sheets.setCurrentWidget(sheet)
            sheet.editor.insertPlainText(text)
            sheet.editor.setFocus()

    def on_double_click(self, item, _col):
        info = self.info(item)
        kind = info.get("kind")
        if kind in ("schema", "category"):
            item.setExpanded(not item.isExpanded())
        elif kind == "object":
            self.tab.open_object(info["owner"], info["name"], info["type"])
        elif kind == "column":
            self.insert_text(q(info["name"]))
        elif kind == "subprogram":
            self.tab.open_object(info["owner"], info["package"], "PACKAGE")

    def on_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        info = self.info(item)
        kind = info.get("kind")
        menu = QMenu(self)
        if kind == "object":
            otype = info["type"]
            full = self.qualified(info["owner"], info["name"])
            open_act = menu.addAction("Abrir", lambda: self.tab.open_object(info["owner"], info["name"], otype))
            menu.setDefaultAction(open_act)
            menu.addAction("Insertar nombre en el editor", lambda: self.insert_text(full))
            menu.addSeparator()
            if otype in WITH_DATA:
                menu.addAction("Ver datos", lambda: self.view_data(info))
            if otype in WITH_COLUMNS:
                menu.addAction("Insertar SELECT con columnas", lambda: self.insert_select(info))
            menu.addAction("Abrir código / DDL en hoja", lambda: self.view_ddl(info))
            menu.addSeparator()
            menu.addAction("Copiar nombre", lambda: QGuiApplication.clipboard().setText(full))
        if kind == "subprogram":
            pkg_name = f"{self.qualified(info['owner'], info['package'])}.{q(info['name'])}"
            menu.addAction("Abrir paquete", lambda: self.tab.open_object(info["owner"], info["package"], "PACKAGE"))
            menu.addAction("Insertar nombre en el editor", lambda: self.insert_text(pkg_name))
        if kind in ("schema", "category") or (kind == "object" and item.childIndicatorPolicy()
                                              == QTreeWidgetItem.ShowIndicator):
            menu.addAction("Refrescar", lambda: self.refresh_item(item))
        if not menu.isEmpty():
            menu.exec(self.tree.viewport().mapToGlobal(pos))

    def refresh_item(self, item):
        info = self.info(item)
        info["loaded"] = False
        info.pop("count", None)
        self.set_info(item, info)
        if info.get("kind") == "category":
            item.setText(0, info["label"])
        item.takeChildren()
        if item.isExpanded():
            self.on_expand(item)
        else:
            item.setExpanded(True)

    def view_data(self, info):
        full = self.qualified(info["owner"], info["name"])
        sheet = self.tab.add_sheet(title=info["name"], text=f"SELECT * FROM {full}")
        sheet.run()

    def insert_select(self, info):
        owner, name = info["owner"], info["name"]

        def ok(rows):
            cols = ",\n       ".join(q(r[0]) for r in rows) or "*"
            self.insert_text(f"SELECT {cols}\nFROM {self.qualified(owner, name)}\n")

        self.tab.meta_call(lambda cur: list_columns(cur, owner, name), ok,
                           lambda m: QMessageBox.critical(self, "Error", m))

    def view_ddl(self, info):
        owner, name, otype = info["owner"], info["name"], info["type"]

        self.tab.meta_call(lambda cur: fetch_ddl(cur, owner, name, otype),
                           lambda ddl: self.tab.add_sheet(title=name, text=ddl),
                           lambda m: QMessageBox.critical(self, "No se pudo obtener el código", m))
