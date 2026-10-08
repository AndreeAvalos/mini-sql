"""Autocompletado y alias."""

from minisql.ui.connection_tab import ConnectionTab
from minisql.ui.editors import SqlEditor

from support import FakeConn, make_session, pump, suggestions


def test_autocomplete(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    # columnas por alias (el FROM puede ir después del cursor)
    sheet.editor.setPlainText("select e. from emp e")
    cur = sheet.editor.textCursor(); cur.setPosition(9); sheet.editor.setTextCursor(cur)
    for _ in range(3):
        sheet.editor.show_completions(True); pump(app, 10)
    names = [sheet.editor.cmodel.item(i).data(SqlEditor.NAME_ROLE) for i in range(sheet.editor.cmodel.rowCount())]
    assert names == ["ID", "NOMBRE"]
    # tablas del usuario y palabras clave por prefijo
    names = suggestions(app, sheet, "select * from em")
    assert names[:2] == ["EMP", "EMPLEO"]
    # columnas sin calificar de las tablas de la sentencia
    assert "DEPTNO" in suggestions(app, sheet, "select * from dept where dep")
    # paquete, secuencia, sinónimo y esquema
    assert suggestions(app, sheet, "begin pkg.") == ["ALTA", "BAJA"]
    assert suggestions(app, sheet, "select seq.") == ["NEXTVAL", "CURRVAL"]
    assert suggestions(app, sheet, "select s_emp.") == ["ID", "NOMBRE"]
    assert suggestions(app, sheet, "select * from scott.") == ["EMP", "EMPLEO", "PKG", "SEQ"]
    # nada dentro de cadenas
    assert suggestions(app, sheet, "select 'em", manual=False) == []


def test_insert_completion_keeps_case(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    suggestions(app, sheet, "select * from em")
    sheet.editor.insert_completion(sheet.editor.cmodel.index(0, 0))
    assert sheet.editor.toPlainText() == "select * from emp e"


def test_table_completion_adds_alias(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    names = suggestions(app, sheet, "select * from em")
    sheet.editor.insert_completion(sheet.editor.cmodel.index(names.index("EMP"), 0))
    assert sheet.editor.toPlainText() == "select * from emp e"
    names = suggestions(app, sheet, "select * from emp e join em")
    sheet.editor.insert_completion(sheet.editor.cmodel.index(names.index("EMPLEO"), 0))
    assert sheet.editor.toPlainText() == "select * from emp e join empleo e2"
    names = suggestions(app, sheet, "select em")              # fuera del FROM: sin alias
    sheet.editor.insert_completion(sheet.editor.cmodel.index(names.index("EMP"), 0))
    assert sheet.editor.toPlainText() == "select emp"


def test_column_completion_adds_alias(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    ed = sheet.editor
    names = suggestions(app, sheet, "select * from emp e join dept d on d.deptno = e.id\nwhere nom")
    assert names == ["NOMBRE"]
    ed.insert_completion(ed.cmodel.index(0, 0))
    assert ed.toPlainText().endswith("where e.nombre")
    # columnas con el mismo nombre en dos tablas: una sugerencia por alias
    suggestions(app, sheet, "SELECT * FROM EMP E, EMP X WHERE ID")
    assert [ed.cmodel.item(i).text().split()[0] for i in range(ed.cmodel.rowCount())] == ["E.ID", "X.ID"]
    ed.insert_completion(ed.cmodel.index(1, 0))
    assert ed.toPlainText() == "SELECT * FROM EMP E, EMP X WHERE X.ID"
    # sin alias, la columna va sola
    suggestions(app, sheet, "select * from emp where nom")
    ed.insert_completion(ed.cmodel.index(0, 0))
    assert ed.toPlainText() == "select * from emp where nombre"
