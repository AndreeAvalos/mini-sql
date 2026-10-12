"""Edición de resultados: qué consultas se pueden editar, validación por tipo, guardar por ROWID, filtrar."""
import datetime
from decimal import Decimal

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from minisql.db.grid import ColumnInfo, apply_changes, parse_value, preview_sql, sql_literal
from minisql.sql.editable import editable_target, rowid_query
from minisql.ui import result_panel as panel_module
from minisql.ui import sheet as sheet_module
from minisql.ui.connection_tab import ConnectionTab
from minisql.ui.results import EditableResultModel, make_matcher

from support import FakeConn, FakeCursor, make_session, wait_until

# ---------------------------------------------------------------- qué se puede editar


@pytest.mark.parametrize("sql, table", [
    ("SELECT * FROM emp", (None, "EMP")),
    ("select e.id, e.nombre n from scott.emp e where e.id > 1 order by 1", ("SCOTT", "EMP")),
    ("select t.*, rownum r from t", (None, "T")),
])
def test_editable_queries(sql, table):
    target, reason = editable_target(sql)
    assert reason is None and (target.owner, target.table) == table


@pytest.mark.parametrize("sql, reason", [
    ("select a.*, b.x from a join b on a.id = b.id", "JOIN"),
    ("select * from a, b", "varias tablas"),
    ("select distinct x from t", "DISTINCT"),
    ("select x, count(*) from t group by x", "GROUP BY"),
    ("select * from t union select * from u", "UNION"),
    ("select sysdate from dual", "DUAL"),
    ("select * from (select * from t)", "directamente"),
    ("update t set x = 1", "no es un SELECT"),
])
def test_not_editable_queries(sql, reason):
    target, why = editable_target(sql)
    assert target is None and reason in why


def test_expressions_are_read_only():
    target, _ = editable_target("select c.id, c.nombre n, upper(c.ciudad) ciudad, c.saldo * 2 doble from clientes c")
    assert target.sources == {"ID": "ID", "N": "NOMBRE"} and target.readonly == {"CIUDAD", "DOBLE"}
    assert target.source_of("N", {"NOMBRE"}) == "NOMBRE" and target.source_of("CIUDAD", {"CIUDAD"}) is None
    assert rowid_query("select * from t;").startswith('SELECT ROWIDTOCHAR(ROWID) AS "MINISQL$ROWID", q.* FROM (')


# ---------------------------------------------------------------- valores permitidos

SALDO = ColumnInfo("SALDO", "NUMBER", 22, 10, 2, 0, None, False)
NOMBRE = ColumnInfo("NOMBRE", "VARCHAR2", 10, None, None, 10, "C", True)
CODIGO = ColumnInfo("CODIGO", "VARCHAR2", 4, None, None, 4, "B", True)
FECHA = ColumnInfo("FECHA", "DATE", 7)
CANT = ColumnInfo("CANT", "NUMBER", 22, 5, 0, 0, None, True)


@pytest.mark.parametrize("col, text, value", [
    (SALDO, "1234.5", Decimal("1234.5")), (SALDO, "1234,56", Decimal("1234.56")),
    (CANT, "42", 42), (NOMBRE, "Ana", "Ana"), (NOMBRE, "", None), (NOMBRE, "   ", "   "),
    (FECHA, "2026-10-12", datetime.datetime(2026, 10, 12)),
    (FECHA, "12/10/2026 14:30", datetime.datetime(2026, 10, 12, 14, 30)),
    (ColumnInfo("R", "RAW", 4), "0a1b", b"\x0a\x1b"),
])
def test_valid_values(col, text, value):
    assert parse_value(text, col) == value


@pytest.mark.parametrize("col, text, message", [
    (SALDO, "abc", "no es un número"), (SALDO, "1.234", "hasta 2 decimales"),
    (SALDO, "123456789", "hasta 8 dígitos enteros"), (SALDO, "", "obligatoria"),
    (CANT, "1.5", "no admite decimales"), (NOMBRE, "x" * 11, "hasta 10 caracteres"),
    (CODIGO, "ñññ", "hasta 4 bytes"),                      # ñ ocupa 2 bytes en UTF-8
    (FECHA, "mañana", "escribe la fecha"), (ColumnInfo("R", "RAW", 2), "zz", "hexadecimal"),
    (ColumnInfo("V", "NUMBER", readonly_reason="es una columna virtual (calculada)"), "1", "virtual"),
])
def test_rejected_values(col, text, message):
    with pytest.raises(ValueError, match=message):
        parse_value(text, col)


# ---------------------------------------------------------------- guardar por ROWID

def test_apply_changes_uses_rowid_binds_and_savepoint():
    cur = FakeCursor()
    FakeCursor.executed.clear()
    rowids = apply_changes(cur, "SCOTT", "EMP", [
        ("update", "ROW1", {"A": Decimal("1.5"), "B": "hola"}),
        ("delete", "ROW2"),
        ("insert", {"A": 9}),
    ])
    sql = [s.strip().lower() for s in FakeCursor.executed]
    assert sql[0] == "savepoint minisql_grilla"
    assert sql[1] == "update scott.emp set a = :v0, b = :v1 where rowid = chartorowid(:rid)"
    assert sql[2] == "delete from scott.emp where rowid = chartorowid(:rid)"
    assert sql[3].startswith("insert into scott.emp (a) values (:v0) returning rowidtochar(rowid)")
    assert rowids and rowids[0].startswith("NEW")


def test_apply_changes_rolls_back_everything_if_a_row_is_gone():
    cur = FakeCursor()
    FakeCursor.executed.clear()
    with pytest.raises(RuntimeError, match="ya no existe"):
        apply_changes(cur, "SCOTT", "EMP", [("update", "ROW1", {"A": 1}), ("delete", "GONE")])
    assert FakeCursor.executed[-1].strip().lower() == "rollback to savepoint minisql_grilla"


# ---------------------------------------------------------------- filtro

def test_filter_matcher():
    contains, eq, gt, null, not_null = (make_matcher(t) for t in ("an", "=ana", ">100", "null", "!null"))
    assert contains("Juana") and not contains(None) and not contains("Pedro")
    assert eq("ANA") and not eq("Juana")
    assert gt(150) and gt(Decimal("100.5")) and not gt(99) and not gt(None)
    assert make_matcher("<=2026-01-31")(datetime.datetime(2026, 1, 31)) and \
        not make_matcher("<=2026-01-31")(datetime.datetime(2026, 2, 1))
    assert null(None) and not null(0) and not_null("x")
    assert make_matcher("") is None


# ---------------------------------------------------------------- en la hoja

@pytest.fixture
def editable_sheet(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    sheet.editor.setPlainText("select * from emp")
    sheet.run()
    assert wait_until(app, lambda: sheet.results.target is not None), sheet.results.state.text()
    return tab, sheet


def cell(sheet, row, col):
    return sheet.results.model.index(row, col)


def test_select_from_one_table_is_editable(editable_sheet):
    _tab, sheet = editable_sheet
    model = sheet.results.model
    assert isinstance(model, EditableResultModel) and "SCOTT.EMP" in sheet.results.state.text()
    assert [r.rowid for r in model.rows] == ["ROW1", "ROW2"]
    assert model.headers == ["A", "B"]                                   # el ROWID no se ve
    assert model.flags(cell(sheet, 0, 0)) & Qt.ItemIsEditable
    assert "NUMBER(10,2) · obligatoria" in model.headerData(0, Qt.Horizontal, Qt.ToolTipRole)
    assert sheet.results.save_btn.isVisible() is False or not sheet.results.save_btn.isEnabled()


def test_edit_validate_save_and_cancel(app, editable_sheet, monkeypatch):
    tab, sheet = editable_sheet
    panel, model = sheet.results, sheet.results.model
    assert not model.setData(cell(sheet, 0, 0), "abc")                   # no es un número: se rechaza
    assert model.rows[0][0] == 1 and "no es un número" in panel.state.text()
    assert not model.setData(cell(sheet, 0, 1), "demasiado")             # B es VARCHAR2(5)
    assert model.setData(cell(sheet, 0, 0), "12.5")
    assert model.data(cell(sheet, 0, 0), Qt.BackgroundRole) == model.edit_colors["modified"]
    assert panel.save_btn.isEnabled() and panel.cancel_btn.isEnabled()

    panel.cancel()                                                       # Cancelar: vuelve a lo leído
    assert model.rows[0][0] == 1 and not model.has_changes()

    model.setData(cell(sheet, 0, 0), "12.5")
    new = model.add_row()
    model.setData(cell(sheet, new, 0), "7")
    model.delete_rows([1])
    assert model.rows[1].deleted and model.summary() == {"modificadas": 1, "nuevas": 1, "borradas": 1}
    shown = []                                                           # la confirmación con el SQL a ejecutar
    monkeypatch.setattr(panel, "confirm_save", lambda stmts, summary: shown.append((stmts, summary)) or True)
    monkeypatch.setattr(FakeConn, "transaction_in_progress", True)       # como Oracle tras un UPDATE
    FakeCursor.executed.clear()
    panel.save()
    stmts, summary = shown[0]
    assert summary["borradas"] == 1 and len(stmts) == 3
    assert stmts[0] == "UPDATE SCOTT.EMP\n   SET A = 12.5\n WHERE ROWID = 'ROW1';"
    assert wait_until(app, lambda: "Guardado" in panel.state.text()), panel.state.text()
    dml = [s.strip().lower() for s in FakeCursor.executed]
    assert dml[0] == "savepoint minisql_grilla"
    assert any(s.startswith("update scott.emp set a = :v0 where rowid") for s in dml)
    assert any(s.startswith("delete from scott.emp where rowid") for s in dml)
    assert any(s.startswith("insert into scott.emp (a) values (:v0)") for s in dml)
    assert tab.pending and "pendiente de Commit" in panel.state.text()   # queda para Commit/Rollback
    assert not model.has_changes() and len(model.rows) == 2              # la borrada se fue, la nueva quedó
    assert model.rows[-1].rowid.startswith("NEW")


def test_new_row_requires_not_null_columns(editable_sheet, monkeypatch):
    _tab, sheet = editable_sheet
    model = sheet.results.model
    new = model.add_row()
    model.setData(cell(sheet, new, 1), "hola")                           # falta A (NOT NULL, sin default)
    warned = []
    monkeypatch.setattr(panel_module.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    sheet.results.save()
    assert warned and "falta A" in warned[0] and model.has_changes()


def test_rerun_with_unsaved_changes_asks(app, editable_sheet, monkeypatch):
    _tab, sheet = editable_sheet
    sheet.results.model.setData(cell(sheet, 0, 0), "3")
    monkeypatch.setattr(sheet_module.QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    FakeCursor.executed.clear()
    sheet.run()
    assert FakeCursor.executed == [] and sheet.results.model.has_changes()     # no se ejecutó nada


def test_read_only_results_explain_why(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    FakeCursor.executed.clear()
    sheet.editor.setPlainText("select a.*, d.b from emp a join dept d on d.id = a.id")
    sheet.run()
    assert wait_until(app, lambda: not sheet.running)
    assert "Solo lectura: tiene JOIN" in sheet.results.state.text()
    assert not any("minisql$rowid" in s.lower() for s in FakeCursor.executed)   # ni siquiera pide el ROWID
    assert not sheet.results.save_btn.isVisible()


def test_filter_in_panel(editable_sheet):
    _tab, sheet = editable_sheet
    panel = sheet.results
    panel.filter_edit.setText("x")
    panel.apply_filter()
    assert panel.count.text() == "1 de 2 filas" and panel.model.rows[0][1] == "x"
    panel.filter_column.setCurrentIndex(1)                                # solo en la columna A
    assert panel.count.text() == "0 de 2 filas"
    panel.filter_edit.setText(">1")
    panel.apply_filter()
    assert panel.count.text() == "1 de 2 filas" and panel.model.rows[0][0] == 2
    new = panel.model.add_row()                                           # una fila nueva se ve aunque filtre
    assert panel.model.rows[new].new


def test_object_viewer_data_tab_is_editable(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    viewer = tab.open_object("SCOTT", "EMP", "TABLE")
    viewer.show_tab("Datos")
    assert wait_until(app, lambda: viewer.data_panel.target is not None), viewer.data_panel.state.text()
    assert viewer.pages["Datos"].model().rowCount() == 2


def test_preview_sql_shows_values_as_literals():
    ops = [("update", "AAA1", {"NOMBRE": "O'Brien", "FECHA": datetime.datetime(2026, 10, 12)}),
           ("insert", {"ID": Decimal("7"), "ALTA": datetime.datetime(2026, 10, 12, 14, 30), "FOTO": b"\x0a\xff"}),
           ("delete", "AAA2")]
    update, insert, delete = preview_sql("SCOTT", "EMP", ops)
    assert "NOMBRE = 'O''Brien'" in update and "FECHA = TO_DATE('2026-10-12', 'YYYY-MM-DD')" in update
    assert update.endswith("WHERE ROWID = 'AAA1';")
    assert insert == ("INSERT INTO SCOTT.EMP (ID, ALTA, FOTO)\nVALUES (7, TO_DATE('2026-10-12 14:30:00', "
                      "'YYYY-MM-DD HH24:MI:SS'), HEXTORAW('0AFF'));")
    assert delete == "DELETE FROM SCOTT.EMP\n WHERE ROWID = 'AAA2';"
    assert sql_literal(None) == "NULL"


def test_cancel_in_preview_saves_nothing(app, editable_sheet, monkeypatch):
    _tab, sheet = editable_sheet
    panel, model = sheet.results, sheet.results.model
    model.setData(cell(sheet, 0, 0), "3")
    dialogs = []

    def fake_exec(dialog):
        dialogs.append(dialog.text.toPlainText())
        return QDialog.Rejected
    monkeypatch.setattr(panel_module.SavePreviewDialog, "exec", fake_exec)
    FakeCursor.executed.clear()
    panel.save()
    assert dialogs and "UPDATE SCOTT.EMP" in dialogs[0] and "SET A = 3" in dialogs[0]
    assert not FakeCursor.executed and model.has_changes()                # no se ejecutó nada
