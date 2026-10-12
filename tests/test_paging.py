"""Paginación de resultados: se traen de Oracle 200 filas por página y las siguientes cuando se piden."""
from PySide6.QtCore import Qt

from minisql.config import PAGE_ROWS
from minisql.ui.connection_tab import ConnectionTab
from minisql.ui.results import ResultModel

from support import FakeConn, FakeCursor, make_session, run_and_wait, wait_until

TOTAL = FakeCursor.big_rows                       # 450 filas en BIG_TABLE: 3 páginas (200 + 200 + 50)


def test_session_fetches_the_first_page_and_keeps_the_cursor_open():
    db = make_session(FakeConn())
    res = db.execute("select * from big_table")
    assert len(res["rows"]) == PAGE_ROWS and res["more"] and res["stream"]
    rows, rowids = res["stream"].fetch(PAGE_ROWS)
    assert rows[0] == (201, "f201") and rowids is None and not res["stream"].done
    closed = FakeCursor.closed
    rows, _ = res["stream"].fetch(PAGE_ROWS)
    assert len(rows) == TOTAL - 2 * PAGE_ROWS and res["stream"].done        # llegó al final: se cierra solo
    assert FakeCursor.closed == closed + 1
    assert res["stream"].fetch(PAGE_ROWS) == ([], None)


def test_small_results_close_the_cursor_right_away():
    db = make_session(FakeConn())
    closed = FakeCursor.closed
    res = db.execute("select a, b from t")
    assert not res["more"] and "stream" not in res and FakeCursor.closed == closed + 1


def test_stream_with_rowid_and_fetch_all():
    db = make_session(FakeConn())
    res = db.execute_with_rowid("select * from big_table")
    assert res["rowids"][:2] == ["R1", "R2"] and res["rows"][0] == (1, "f1")
    rows, rowids = res["stream"].fetch_all(10_000)
    assert len(rows) == TOTAL - PAGE_ROWS and rowids[0] == "R201" and rows[0] == (201, "f201")


def test_fetch_all_respects_the_limit():
    db = make_session(FakeConn())
    res = db.execute("select * from big_table")
    rows, _ = res["stream"].fetch_all(30)
    assert len(rows) == 30 and not res["stream"].done


def test_model_pages_over_loaded_rows():
    model = ResultModel(["N"], [(i,) for i in range(1, 451)], page_size=200)
    assert model.page_count() == 3 and len(model.rows) == 200
    model.set_page(2)
    assert model.rows[0] == (401,) and len(model.rows) == 50
    assert model.headerData(0, Qt.Vertical) == 401                         # numeración global
    model.set_filter(">440")                                               # el filtro vuelve a la página 1
    assert model.page == 0 and len(model.rows) == 10
    assert ResultModel(["N"], [(i,) for i in range(500)]).page_count() == 1   # sin page_size: todo


def test_sheet_pages_through_a_big_table(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    run_and_wait(app, sheet, "select * from big_table")
    panel = sheet.results
    assert wait_until(app, lambda: panel.target is not None)                # sigue siendo editable
    model = panel.model
    assert len(model.rows) == PAGE_ROWS and panel.count.text() == "1-200 · 200+ filas"
    assert "primera página" in sheet.status.text()
    assert panel.next_btn.isEnabled() and not panel.prev_btn.isEnabled() and panel.all_btn.isEnabled()

    panel.next_page()                                                      # trae la página 2 de Oracle
    assert wait_until(app, lambda: model.page == 1)
    assert model.rows[0][0] == 201 and model.headerData(0, Qt.Vertical) == 201
    assert panel.count.text() == "201-400 · 400+ filas"

    panel.next_page()                                                      # la última: el cursor se cierra
    assert wait_until(app, lambda: model.page == 2)
    assert len(model.rows) == TOTAL - 2 * PAGE_ROWS and not panel.has_more()
    assert panel.count.text() == "401-450 · 450 filas" and not panel.next_btn.isEnabled()

    panel.prev_page()
    assert model.page == 1 and model.rows[0][0] == 201                     # ya cargada: sin ir a Oracle

    assert model.setData(model.index(0, 0), "7")                           # editar en otra página
    assert model.pending_ops() == [("update", "R201", {"A": 7})]


def test_fetch_all_and_new_rows_go_to_the_last_page(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    run_and_wait(app, sheet, "select * from big_table")
    panel = sheet.results
    assert wait_until(app, lambda: panel.target is not None)
    panel.fetch_all()
    assert wait_until(app, lambda: not panel.fetching and not panel.has_more())
    model = panel.model
    assert len(model.all_rows) == TOTAL and model.page == 0 and panel.count.text() == "1-200 · 450 filas"
    new = model.add_row()
    assert model.page == 2 and model.rows[new].new
    model.add_row()                                                        # dos filas nuevas iguales (vacías)
    model.delete_rows([new])                                               # quita la correcta
    assert sum(1 for r in model.all_rows if r.new) == 1


def test_new_query_or_closing_the_sheet_releases_the_cursor(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    run_and_wait(app, sheet, "select * from big_table")
    stream = sheet.results.stream
    run_and_wait(app, sheet, "select a, b from t")                        # otro resultado: el cursor se cierra
    assert wait_until(app, lambda: stream.done) and sheet.results.stream is None
    run_and_wait(app, sheet, "select * from big_table")
    stream = sheet.results.stream
    tab.add_sheet()
    tab.close_sheet(tab.sheets.indexOf(sheet))
    assert wait_until(app, lambda: stream.done)


def test_scripts_show_only_the_first_page(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    run_and_wait(app, sheet, "insert into t values (1);\nselect * from big_table;")
    assert len(sheet.results.model.rows) == PAGE_ROWS and sheet.results.stream is None
