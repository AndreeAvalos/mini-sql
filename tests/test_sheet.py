"""Hoja de SQL: ejecución, formato, colores y DBMS_OUTPUT."""

from minisql.ui.connection_tab import ConnectionTab
from minisql.ui.highlighter import SqlHighlighter

from support import FakeConn, FakeCursor, make_session, pump, run_and_wait, wait_until


def test_format_sheet_is_undoable(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    sheet.editor.setPlainText("select 1 from dual;\n\nselect a,b from t where x=1")
    cur = sheet.editor.textCursor()
    cur.setPosition(len(sheet.editor.toPlainText()))
    sheet.editor.setTextCursor(cur)
    sheet.format_current()
    text = sheet.editor.toPlainText()
    assert text.startswith("select 1 from dual;\n\nSELECT a,")
    sheet.editor.undo()
    assert sheet.editor.toPlainText().endswith("select a,b from t where x=1")


def test_highlighter_states(app):
    from PySide6.QtGui import QTextDocument
    doc = QTextDocument("select 'a\nb' from t /* x\ny */ where 1=1")
    SqlHighlighter(doc, dark=True).rehighlight()
    assert [doc.findBlockByNumber(i).userState() for i in range(3)] == [2, 1, 0]


def test_running_blocks_the_previous_results(app):
    """El "Ejecutando…" va sobre la tabla (no en la barra) y no deja tocar los resultados anteriores."""
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    tab.resize(900, 600)
    tab.show()
    run_and_wait(app, sheet, "select * from emp")
    panel = sheet.results
    assert wait_until(app, lambda: panel.target is not None)
    sheet.set_running(True, "Ejecutando…")
    overlay = panel.overlay
    assert panel.busy and overlay.isVisible() and overlay.timer.isActive()
    assert "Ejecutando…" in overlay.label.text() and " s" in overlay.label.text()
    assert "Ejecutando" not in sheet.status.text()                   # ya no en la barra
    assert overlay.geometry() == panel.rect() and overlay.cancel_btn.isVisible()
    assert not panel.view.isEnabled() and not panel.filter_edit.isEnabled()
    model = panel.model
    panel.add_row()                                                   # ni con atajos ni con el menú
    panel.delete_rows()
    assert not model.has_changes() and not panel.add_btn.isEnabled() and panel.add_btn.isVisible()
    cancelled = []
    sheet.conn_tab.db.cancel = lambda: cancelled.append(True)
    overlay._on_cancel = sheet.conn_tab.db.cancel
    overlay.cancel_btn.click()
    assert cancelled and "Cancelando" in overlay.label.text()
    sheet.set_running(False)
    assert not panel.busy and not overlay.isVisible() and panel.view.isEnabled() and panel.add_btn.isEnabled()


def test_sheet_runs_query(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    sheet.editor.setPlainText("select 1 from dual;")
    sheet.run()
    pump(app)
    assert sheet.model.rowCount() == 2


def test_sheet_shows_dbms_output(app):
    FakeCursor.buffer = []
    FakeCursor.executed.clear()
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    run_and_wait(app, sheet, "begin\n  dbms_output.put_line('hola');\n  dbms_output.put_line('mundo');\nend;")
    assert sheet.output.toPlainText().endswith("hola\nmundo")
    assert sheet.result_tabs.tabText(1) == "DBMS_OUTPUT (2)"
    assert sheet.result_tabs.currentWidget() is sheet.output          # el bloque imprimió: se muestra
    # un SELECT vuelve a la pestaña de resultados; la salida anterior se conserva
    run_and_wait(app, sheet, "select 1 from dual")
    assert sheet.result_tabs.currentWidget() is sheet.results
    assert "hola" in sheet.output.toPlainText() and sheet.result_tabs.tabText(1) == "DBMS_OUTPUT"
    # DBMS_OUTPUT.ENABLE solo una vez por sesión
    assert sum("dbms_output.enable" in x.lower() for x in FakeCursor.executed) == 1
    sheet.clear_output()
    assert sheet.output.toPlainText() == ""


def test_dbms_output_is_shown_even_on_error(app):
    FakeCursor.buffer = []
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    run_and_wait(app, sheet, "begin dbms_output.put_line('paso 1'); raise_application_error(-20001, 'x'); end;")
    assert "ORA-20001" in sheet.status.text()
    assert "paso 1" in sheet.output.toPlainText()
    assert sheet.result_tabs.currentWidget() is sheet.output


def test_dbms_output_can_be_hidden(app):
    FakeCursor.buffer = []
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    sheet.output_check.setChecked(False)
    assert not sheet.result_tabs.isTabVisible(1)
    run_and_wait(app, sheet, "begin dbms_output.put_line('oculto'); end;")
    assert sheet.output.toPlainText() == "" and FakeCursor.buffer == []   # se leyó pero no se muestra


def test_sheet_accepts_sqlplus_commands(app):
    FakeCursor.buffer = []
    FakeCursor.executed.clear()
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    sheet.output_check.setChecked(False)
    run_and_wait(app, sheet, "SET SERVEROUTPUT ON\nexec dbms_output.put_line('hola');")
    assert sheet.output_check.isChecked()
    assert "BEGIN\n  dbms_output.put_line('hola');\nEND;" in FakeCursor.executed
    assert "hola" in sheet.output.toPlainText()
    sheet.editor.setPlainText("set serveroutput off")
    sheet.run()
    assert not sheet.output_check.isChecked() and "desactivado" in sheet.status.text()
