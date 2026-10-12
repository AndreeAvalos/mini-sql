"""Varias sentencias en un bloque o selección: se separan y se ejecutan en orden, como un script."""

import pytest
from PySide6.QtWidgets import QMessageBox

from minisql.db.session import ScriptError
from minisql.sql.text import split_script, split_statements
from minisql.ui.connection_tab import ConnectionTab

from support import FakeConn, FakeCursor, make_session, run_and_wait

INSERTS = """-- =====================================================
-- SF2 - Seguro Facil Audífonos (ID 26)
-- =====================================================
-- 1er año Daños
INSERT INTO DIS_ASOCIACION_SEGUROS(ID_SEGURO_DESTINO, ID_SEGURO_ORIGEN, CREADO_POR, CREADO_EL)
VALUES(26, 42, 'DISTELSA', SYSDATE); -- SFCA1
INSERT INTO DIS_ASOCIACION_SEGUROS(ID_SEGURO_DESTINO, ID_SEGURO_ORIGEN, CREADO_POR, CREADO_EL)
VALUES(26, 43, 'DISTELSA', SYSDATE); -- SFCB1
-- 2do año completo
INSERT INTO DIS_ASOCIACION_SEGUROS(ID_SEGURO_DESTINO, ID_SEGURO_ORIGEN, CREADO_POR, CREADO_EL)
VALUES(26, 31, 'DISTELSA', SYSDATE); -- SFCA6
INSERT INTO DIS_ASOCIACION_SEGUROS(ID_SEGURO_DESTINO, ID_SEGURO_ORIGEN, CREADO_POR, CREADO_EL)
VALUES(26, 32, 'DISTELSA', SYSDATE); -- SFCB6"""


def test_split_inserts_with_trailing_comments():
    stmts = split_statements(INSERTS)
    assert len(stmts) == 4
    assert all(s.startswith("INSERT INTO DIS_ASOCIACION_SEGUROS") and s.endswith("SYSDATE)") for s in stmts)
    assert "VALUES(26, 31," in stmts[2]


def test_split_ignores_semicolons_in_literals_and_comments():
    sql = "select 'a;b', q'[it's; ok]', \"X;Y\" from t; /* ; */ -- ;\nselect 2 from dual"
    assert split_statements(sql) == ["select 'a;b', q'[it's; ok]', \"X;Y\" from t", "select 2 from dual"]


def test_split_keeps_plsql_blocks_whole():
    sql = ("BEGIN\n  x := 1;\n  dbms_output.put_line('a');\nEND;\n/\n"
           "CREATE OR REPLACE PROCEDURE p IS\nBEGIN\n  NULL;\nEND;\n/\nselect 1 from dual;")
    assert split_statements(sql) == ["BEGIN\n  x := 1;\n  dbms_output.put_line('a');\nEND;",
                                     "CREATE OR REPLACE PROCEDURE p IS\nBEGIN\n  NULL;\nEND;",
                                     "select 1 from dual"]
    assert split_statements("BEGIN\n  NULL;\nEND;") == ["BEGIN\n  NULL;\nEND;"]   # sin '/' al final


def test_split_script_handles_sqlplus_commands():
    serveroutput, stmts = split_script("SET SERVEROUTPUT ON;\nEXEC p(1);\nselect 1 from dual;\n-- fin")
    assert serveroutput is True
    assert stmts == ["BEGIN\n  p(1);\nEND;", "select 1 from dual"]
    assert split_script("-- solo un comentario\n;\n/") == (None, [])


def test_session_runs_script_in_order():
    db = make_session(FakeConn())
    FakeCursor.executed = []
    res = db.execute_script(["insert into t values (1)", "update t set a = 2 where a = 1", "select a, b from t"])
    assert [s for s in FakeCursor.executed if "dbms_output" not in s] == [
        "insert into t values (1)", "update t set a = 2 where a = 1", "select a, b from t"]
    assert res["script"] == 3 and res["headers"] == ["A", "B"]      # muestra la última consulta
    assert res["message"].startswith("3 sentencias ejecutadas")


def test_session_script_stops_at_first_error():
    db = make_session(FakeConn())
    FakeCursor.executed = []
    with pytest.raises(ScriptError) as err:
        db.execute_script(["insert into t values (1)", "begin raise_application_error(-20001, 'x'); end;",
                           "insert into t values (3)"])
    msg = str(err.value)
    assert msg.startswith("Sentencia 2 de 3:") and "ORA-20001" in msg and "Las 1 anteriores" in msg
    assert "insert into t values (3)" not in FakeCursor.executed


def test_sheet_runs_block_of_inserts_as_script(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    FakeCursor.executed = []
    run_and_wait(app, sheet, INSERTS)
    inserts = [s for s in FakeCursor.executed if s.startswith("INSERT")]
    assert len(inserts) == 4 and not any(";" in s for s in inserts)
    assert "4 sentencias ejecutadas" in sheet.status.text()


def test_sheet_script_failure_says_which_statement(app, monkeypatch):
    monkeypatch.setattr(FakeConn, "transaction_in_progress", True)
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    run_and_wait(app, sheet, "insert into t values (1);\nbegin raise_application_error(-20001, 'x'); end;\n/")
    assert "Sentencia 2 de 2" in sheet.status.text()
    assert tab.pending                                  # el primer INSERT quedó pendiente de Commit


def test_sheet_script_confirms_dangerous_statement_anywhere(app, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    FakeCursor.executed = []
    sheet.editor.setPlainText("insert into t values (1);\ndelete from t;")
    sheet.run()
    assert asked and "Sin WHERE" in asked[0]
    assert not sheet.running and not FakeCursor.executed

    asked.clear()
    sheet.editor.setPlainText("insert into t values (1);\ncreate table x (a number);")
    sheet.run()
    assert asked and "COMMIT automático" in asked[0]
