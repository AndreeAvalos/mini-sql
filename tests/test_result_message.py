"""Lo que hizo una sentencia sin filas (o su error) se ve en el área de resultados, no solo en la barra."""
import pytest

from minisql.sql.text import first_keyword, result_message
from minisql.ui.connection_tab import ConnectionTab

from support import FakeConn, make_session, run_and_wait


@pytest.mark.parametrize("sql, rows, expected", [
    ("update t set a = 1 where b = 2", 3, "3 filas actualizadas"),
    ("UPDATE t SET a = 1 WHERE b = 2", 1, "1 fila actualizada"),
    ("delete from t where a = 1", 0, "0 filas eliminadas"),
    ("insert into t values (1)", 1, "1 fila insertada"),
    ("merge into t using s on (t.id = s.id) when matched then update set t.a = s.a", 5, "5 filas combinadas"),
    ("create table clientes_tmp (id number)", 0, "Tabla clientes_tmp creada"),
    ("CREATE OR REPLACE PACKAGE BODY ventas.pkg AS END;", 0, "Cuerpo del paquete ventas.pkg creado"),
    ("create unique index ix_t on t (a)", 0, "Índice ix_t creado"),
    ("drop view v_clientes", 0, "Vista v_clientes eliminada"),
    ("alter sequence seq increment by 2", 0, "Secuencia seq modificada"),
    ("create public database link tf052 connect to u identified by p using 'X'", 0, "Database link tf052 creado"),
    ("truncate table t", 0, "Tabla vaciada (TRUNCATE)"),
    ("grant select on t to otro", 0, "Permisos otorgados"),
    ("begin pkg.alta; end;", 0, "Bloque PL/SQL ejecutado"),
    ("-- comentario\ncommit", 0, "Commit realizado"),
])
def test_result_message(sql, rows, expected):
    assert result_message(sql, first_keyword(sql), rows) == expected


def sheet_with_results(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "DEV")
    sheet = tab.current_sheet()
    run_and_wait(app, sheet, "select a, b from t")                       # una tabla de antes
    return tab, sheet


def test_update_shows_what_happened_instead_of_the_old_grid(app, monkeypatch):
    monkeypatch.setattr(FakeConn, "transaction_in_progress", True)
    _tab, sheet = sheet_with_results(app)
    panel = sheet.results
    assert panel.stack.currentWidget() is panel.view
    run_and_wait(app, sheet, "update t set a = 1 where b = 2")
    assert panel.stack.currentWidget() is panel.message and sheet.result_tabs.currentWidget() is panel
    assert panel.message.title.text() == "1 fila actualizada"
    assert "pendiente de Commit" in panel.message.detail.text() and " s" in panel.message.detail.text()
    assert panel.model.rowCount() == 0 and not panel.add_btn.isVisible()  # nada editable de la consulta vieja
    run_and_wait(app, sheet, "select a, b from t")                       # una consulta vuelve a la tabla
    assert panel.stack.currentWidget() is panel.view and panel.model.rowCount() == 2


def test_errors_show_in_the_results_area(app):
    _tab, sheet = sheet_with_results(app)
    run_and_wait(app, sheet, "begin raise_application_error(-20001, 'x'); end;")
    panel = sheet.results
    assert panel.stack.currentWidget() is panel.message and "ORA-20001" in panel.message.title.text()
    assert panel.message.icon.text() == "✖"
