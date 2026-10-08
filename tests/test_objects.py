"""Explorador, visor de objetos, F4 y compilación."""

from minisql.ui.connection_tab import ConnectionTab
from minisql.ui.object_viewer import ObjectViewer
from minisql.ui.sheet import SheetWidget

from support import FakeConn, FakeCursor, make_session, pump, wait_until


def test_explorer_tree(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    pump(app)
    tree = tab.explorer.tree
    schema = tree.topLevelItem(0)
    assert schema.text(0) == "SCOTT"
    tables = schema.child(0)
    tables.setExpanded(True)
    pump(app)
    assert tables.text(0) == "Tablas (2)"
    emp = tables.child(0)
    emp.setExpanded(True)
    pump(app)
    assert emp.child(0).text(0).startswith("🔑 ID")
    tab.explorer.filter.setText("dep")
    assert tables.text(0) == "Tablas (1/2)"


def test_object_viewer(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    viewer = tab.open_object("SCOTT", "EMP", "TABLE")
    pump(app)
    cols = viewer.pages["Columnas"].model()
    assert cols.rowCount() == 2 and cols.rows[0][1] == "ID" and cols.rows[0][5] == "🔑"
    assert cols.rows[1][2] == "VARCHAR2(50)" and cols.rows[1][4] == "'x'"
    viewer.tabs.setCurrentWidget(viewer.pages["Datos"])
    pump(app)
    assert viewer.pages["Datos"].model().rowCount() == 2
    viewer.tabs.setCurrentWidget(viewer.pages["Detalles"])
    pump(app)
    props = dict(viewer.pages["Detalles"].model().rows)
    assert props["Nombre"] == "EMP" and props["Estado"] == "VALID"
    viewer.tabs.setCurrentWidget(viewer.pages["DDL"])
    pump(app)
    assert "CREATE TABLE" in viewer.pages["DDL"].toPlainText()
    # abrir otra vez el mismo objeto no duplica la pestaña; la hoja de SQL sigue disponible
    count = tab.sheets.count()
    assert tab.open_object("SCOTT", "EMP", "TABLE") is viewer and tab.sheets.count() == count
    assert isinstance(tab.current_sheet(), SheetWidget)
    # cerrar el visor deja la hoja
    tab.close_sheet(tab.sheets.indexOf(viewer))
    assert tab.sheets.count() == count - 1 and isinstance(tab.current_sheet(), SheetWidget)


def test_explorer_double_click_opens_object(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    pump(app)
    tables = tab.explorer.tree.topLevelItem(0).child(0)
    tables.setExpanded(True)
    pump(app)
    tab.explorer.on_double_click(tables.child(0), 0)
    viewer = tab.sheets.currentWidget()
    assert isinstance(viewer, ObjectViewer) and viewer.key == ("SCOTT", "EMP", "TABLE")


def test_f4_opens_object_under_cursor(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    sheet.editor.setPlainText("select * from s_emp e where e.id = 1")
    for pos, expected in ((16, ("SCOTT", "EMP", "TABLE")), (30, ("SCOTT", "EMP", "TABLE"))):
        cur = sheet.editor.textCursor()
        cur.setPosition(pos)
        sheet.editor.setTextCursor(cur)
        sheet.open_object_at_cursor()
        pump(app)
        assert tab.sheets.currentWidget().key == expected
        tab.sheets.setCurrentWidget(sheet)


def test_edit_and_compile_package(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    viewer = tab.open_object("SCOTT", "PKG", "PACKAGE")
    code = viewer.pages["Código"]
    assert wait_until(app, lambda: "PACKAGE BODY PKG" in code.toPlainText())
    assert code.toPlainText().startswith("CREATE OR REPLACE PACKAGE PKG AS")
    assert not code.isReadOnly() and not viewer.has_unsaved()
    new_text = code.toPlainText().replace("NULL;", "NULL")
    code.selectAll()
    code.insertPlainText(new_text)                    # error de sintaxis, como al teclear
    assert viewer.has_unsaved()
    FakeCursor.executed.clear()
    FakeCursor.errors = [("PACKAGE BODY", 4, 5, "PLS-00103: Encountered END")]
    try:
        viewer.compile()
        assert wait_until(app, lambda: "Compilado" in viewer.status.text())
    finally:
        FakeCursor.errors = []
    ran = [x.strip().lower() for x in FakeCursor.executed]
    assert ran[0] == "alter session set current_schema = scott"
    assert ran[1].startswith("create or replace package pkg as")
    assert ran[2].startswith("create or replace package body pkg as") and "/" not in ran[2]
    assert ran[3] == "alter session set current_schema = scott"
    assert "1 error" in viewer.status.text() and not viewer.has_unsaved()
    assert viewer.errors.model().rows == [(9, 5, "PLS-00103: Encountered END")]
    assert code.textCursor().blockNumber() + 1 == 9                      # salta al error
