"""Protección contra DELETE/UPDATE sin WHERE y contra ejecutar algo distinto de lo que se ve."""
import pytest
from PySide6.QtWidgets import QMessageBox

from minisql.sql.text import changes_without_where
from minisql.ui import sheet as sheet_module
from minisql.ui.connection_tab import ConnectionTab

from support import FakeConn, FakeCursor, make_session, pump, wait_until


@pytest.mark.parametrize("sql, expected", [
    ("DELETE FROM clientes", ["DELETE FROM clientes"]),
    ("delete from clientes where id = 1", []),
    ("UPDATE clientes SET saldo = (SELECT MAX(s) FROM x WHERE x.id = 1)",       # WHERE solo en el subquery
     ["UPDATE clientes SET saldo = (SELECT MAX(s) FROM x WHERE x.id = 1)"]),
    ("BEGIN\n  DELETE FROM clientes;\nEND;", ["DELETE FROM clientes"]),            # dentro de PL/SQL
    ("BEGIN\n  UPDATE t SET a = 1 WHERE id = 2;\n  DELETE FROM log;\nEND;", ["DELETE FROM log"]),
    ("IF v > 0 THEN UPDATE t SET y = 2; END IF;", ["UPDATE t SET y = 2"]),
    ("DELETE FROM clientes -- WHERE id = 5", ["DELETE FROM clientes"]),            # el WHERE está comentado
    ("DELETE FROM clientes /* WHERE id = 5 */", ["DELETE FROM clientes"]),
    ("DELETE FROM t WHERE nombre = 'x; where'", []),                               # ; y where dentro de un texto
    ("SELECT * FROM t FOR UPDATE", []),
    ("CREATE TRIGGER x BEFORE INSERT OR UPDATE OR DELETE ON t FOR EACH ROW BEGIN NULL; END;", []),
    ("ALTER TABLE h ADD CONSTRAINT fk FOREIGN KEY (p) REFERENCES p(id) ON DELETE CASCADE", []),
    ("MERGE INTO t USING s ON (t.id = s.id) WHEN MATCHED THEN UPDATE SET t.v = s.v", []),
])
def test_changes_without_where(sql, expected):
    assert changes_without_where(sql) == expected


@pytest.fixture
def answers(monkeypatch):
    """Registra las confirmaciones que pide la hoja y responde lo indicado (No por defecto)."""
    asked = []
    reply = {"value": QMessageBox.No}

    def warning(_parent, _title, text, *_a, **_k):
        asked.append(text)
        return reply["value"]

    monkeypatch.setattr(sheet_module.QMessageBox, "warning", staticmethod(warning))
    return asked, reply


def run(app, sheet, text):
    FakeCursor.executed.clear()
    sheet.editor.setPlainText(text)
    sheet.run()
    wait_until(app, lambda: not sheet.running, 2)
    return [s for s in FakeCursor.executed if "dbms_output" not in s.lower()]


@pytest.mark.parametrize("sql", [
    "BEGIN\n  DELETE FROM clientes;\nEND;",
    "UPDATE clientes SET saldo = (SELECT MAX(s) FROM x WHERE x.id = 1)",
    "DECLARE\n  v NUMBER;\nBEGIN\n  UPDATE clientes SET activo = 'N';\nEND;",
])
def test_dangerous_statements_ask_and_do_not_run_if_refused(app, answers, sql):
    asked, _reply = answers
    sheet = ConnectionTab(make_session(FakeConn()), "T", "PROD").current_sheet()
    assert run(app, sheet, sql) == []                       # se dijo que no: no se ejecutó nada
    assert len(asked) == 1 and "TODAS las filas" in asked[0]


def test_confirmed_statement_runs_and_safe_ones_do_not_ask(app, answers):
    asked, reply = answers
    sheet = ConnectionTab(make_session(FakeConn()), "T", "PROD").current_sheet()
    reply["value"] = QMessageBox.Yes
    assert run(app, sheet, "DELETE FROM log") == ["DELETE FROM log"]
    asked.clear()
    with_where = "DELETE FROM log WHERE fecha < SYSDATE - 30"
    assert run(app, sheet, with_where) == [with_where]
    ran = run(app, sheet, "select * from clientes")          # se ejecuta envuelta para traer el ROWID
    assert any("select * from clientes" in s for s in ran)
    assert asked == []                                       # con WHERE o un SELECT no pregunta


def test_running_statement_is_highlighted_and_brought_into_view(app, answers):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    tab.resize(800, 500)
    tab.show()
    sheet = tab.current_sheet()
    text = "select 1 from dual" + "\n\n-- relleno" * 80 + "\n\nselect * from clientes"
    sheet.editor.setPlainText(text)
    cursor = sheet.editor.textCursor()
    cursor.setPosition(len(text))                            # el cursor al final…
    sheet.editor.setTextCursor(cursor)
    sheet.editor.verticalScrollBar().setValue(0)             # …pero la vista arriba (no se ve qué va a correr)
    pump(app, 3)
    sheet.run()
    run_layer = sheet.editor.highlight_layers["run"]
    assert run_layer and run_layer[0].cursor.selectedText() == "select * from clientes"
    start = sheet.editor.document().find("select * from clientes")
    assert sheet.editor.viewport().rect().contains(sheet.editor.cursorRect(start).topLeft())   # ahora se ve
    assert sheet.editor.textCursor().position() == len(text)                                  # el cursor no se movió
    assert wait_until(app, lambda: not sheet.editor.highlight_layers["run"], 3)               # el resaltado se va
