"""Genera las capturas del README con datos simulados (no se conecta a ninguna base).

    python docs/generar_capturas.py

Abre ventanas por unos segundos y guarda los PNG en docs/img/.
"""
import datetime
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from PySide6.QtCore import QPoint
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

from minisql.ui.main_window import MainWindow

import support
from support import FakeConn, FakeCursor, FakeDebugSession, make_session, pump, wait_until

OUT = ROOT / "docs" / "img"
SIZE = (1280, 760)

QUERY = """SELECT c.id,
       c.nombre,
       c.ciudad,
       c.saldo,
       c.fecha_alta
FROM clientes c
WHERE c.saldo > 1000
ORDER BY c.saldo DESC"""

CLIENTES = [
    (1042, "Comercial Los Altos", "Guatemala", Decimal("58230.50"), datetime.date(2019, 3, 14)),
    (2318, "Distribuidora El Sol", "Quetzaltenango", Decimal("41870.00"), datetime.date(2020, 7, 2)),
    (877, "Ferretería Central", "Escuintla", Decimal("35410.75"), datetime.date(2018, 11, 23)),
    (3301, "Supermercado La Paz", "Antigua Guatemala", Decimal("29999.99"), datetime.date(2021, 1, 9)),
    (1520, "Panadería San José", "Cobán", Decimal("18240.00"), datetime.date(2017, 5, 30)),
    (2777, "Agroservicios del Norte", "Petén", Decimal("12505.10"), datetime.date(2022, 9, 18)),
    (402, "Librería Universitaria", "Guatemala", Decimal("9870.40"), None),
    (3920, "Óptica Visión Clara", "Mazatenango", Decimal("4310.00"), datetime.date(2023, 2, 1)),
]


class DemoCursor(FakeCursor):
    """Datos de ejemplo para que las capturas se vean como un uso real."""

    def execute(self, sql, *args, **kwargs):
        super().execute(sql, *args, **kwargs)
        if "from clientes" in self.sql:
            self.description = [("ID",), ("NOMBRE",), ("CIUDAD",), ("SALDO",), ("FECHA_ALTA",)]

    def fetchmany(self, n):
        return CLIENTES if "from clientes" in self.sql else super().fetchmany(n)

    def fetchall(self):
        q = self.sql
        n = self.kw.get("n") or self.kw.get("nam") or self.kw.get("tab")
        if "all_users" in q:
            return [("VENTAS",), ("CONTABILIDAD",), ("INVENTARIO",)]
        if q.lstrip().startswith("select object_name, object_type from all_objects"):
            return [("CLIENTES", "TABLE"), ("CLIENTES_HIST", "TABLE"), ("PKG", "PACKAGE")]
        if "object_type = 'table'" in q:
            return [("CLIENTES", "VALID"), ("FACTURAS", "VALID"), ("PEDIDOS", "VALID"), ("PRODUCTOS", "VALID")]
        if "all_col_comments" in q:
            return [(1, "ID", "NUMBER", 22, 10, 0, 0, "N", None, "Código del cliente"),
                    (2, "NOMBRE", "VARCHAR2", 120, None, None, 120, "N", None, "Razón social"),
                    (3, "CIUDAD", "VARCHAR2", 60, None, None, 60, "Y", None, None),
                    (4, "SALDO", "NUMBER", 22, 14, 2, 0, "Y", "0 ", "Saldo pendiente en quetzales"),
                    (5, "FECHA_ALTA", "DATE", 7, None, None, 0, "Y", "SYSDATE ", None)]
        if "all_tab_columns" in q and "nullable" not in q and n == "CLIENTES":
            return [("ID", "NUMBER", 22, 10, 0, 0), ("NOMBRE", "VARCHAR2", 120, None, None, 120),
                    ("CIUDAD", "VARCHAR2", 60, None, None, 60), ("SALDO", "NUMBER", 22, 14, 2, 0),
                    ("FECHA_ALTA", "DATE", 7, None, None, 0)]
        if "all_tab_columns" in q and n == "CLIENTES":
            return [("ID", "NUMBER", 22, 10, 0, 0, "N", "P"), ("NOMBRE", "VARCHAR2", 120, None, None, 120, "N", None),
                    ("CIUDAD", "VARCHAR2", 60, None, None, 60, "Y", None), ("SALDO", "NUMBER", 22, 14, 2, 0, "Y", None)]
        if q.lstrip().startswith("select object_type from all_objects") and n == "CLIENTES":
            return [("TABLE",)]
        return super().fetchall()


class DemoConn(FakeConn):
    username, dsn, version = "ventas", "DESARROLLO", "19.21.0.0.0"

    def cursor(self):
        return DemoCursor()


def window():
    w = MainWindow(session_path=tempfile.mkdtemp(), restore=False)
    w.keeper.stop()
    w.resize(*SIZE)
    w.show()
    tab = w.add_connection(make_session(DemoConn(), DemoConn(), opener=DemoConn), "DESARROLLO", "DESARROLLO")
    pump(app, 10)
    tab.explorer.tree.topLevelItem(0).child(0).setExpanded(True)    # VENTAS > Tablas
    pump(app, 10)
    return w, tab


def save(widget, name, popup=None):
    image = widget.grab()
    if popup is not None and popup.isVisible():          # el popup es otra ventana: se pinta encima
        painter = QPainter(image)
        painter.drawPixmap(widget.mapFromGlobal(popup.mapToGlobal(QPoint(0, 0))), popup.grab())
        painter.end()
    image.save(str(OUT / name))
    print("guardada", OUT / name)


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    # 1. Hoja con resultados
    w, tab = window()
    sheet = tab.current_sheet()
    sheet.editor.setPlainText(QUERY)
    sheet.run()
    wait_until(app, lambda: not sheet.running)
    pump(app, 10)
    save(w, "hoja.png")

    # 2. Autocompletado con alias
    sheet.editor.setPlainText("SELECT *\nFROM clientes c\nWHERE c.")
    cur = sheet.editor.textCursor()
    cur.movePosition(cur.MoveOperation.End)
    sheet.editor.setTextCursor(cur)
    sheet.editor.setFocus()
    for _ in range(3):
        sheet.editor.show_completions(manual=True)
        pump(app, 10)
    save(w, "autocompletado.png", sheet.editor.completer.popup())
    sheet.editor.hide_completions()

    # 3. Visor de una tabla
    tab.open_object("VENTAS", "CLIENTES", "TABLE")
    pump(app, 20)
    save(w, "objeto.png")

    # 4. Depuración detenida en un breakpoint
    tab.debugger.session_factory = FakeDebugSession
    viewer = tab.open_object("SCOTT", "PKG", "PACKAGE")
    code = viewer.pages["Código"]
    wait_until(app, lambda: "PACKAGE BODY" in code.toPlainText())
    code.toggle_breakpoint(9)
    FakeDebugSession.script = [4, None]
    FakeDebugSession.variables = lambda self, names: [("P_CLIENTE", "1042"), ("V_SALDO", "58230.5"),
                                                      ("V_ESTADO", "ACTIVO")]
    tab.start_debug("BEGIN\n  SCOTT.PKG.ALTA;\nEND;", [], target=("PACKAGE BODY", "SCOTT", "PKG"))
    wait_until(app, lambda: code.exec_line == 9)
    pump(app, 10)
    save(w, "depuracion.png")
    tab.debug_panel.do("continue")
    wait_until(app, lambda: not tab.debugger.active)
    w.keeper.stop()


if __name__ == "__main__":
    support.FakeCursor.buffer = []
    main()
