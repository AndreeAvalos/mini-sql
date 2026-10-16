"""Desconectar y reconectar una conexión sin cerrar su pestaña, y detectar cuando Oracle corta la sesión."""
import threading

import pytest
from PySide6.QtWidgets import QMessageBox

from minisql.db.session import NotConnected, SessionBusy, connection_lost
from minisql.ui import connection_tab as tab_module
from minisql.ui import sheet as sheet_module
from minisql.ui.main_window import MainWindow

from support import FakeConn, make_session, run_and_wait, wait_until


def test_session_disconnect_and_reconnect():
    opened = []

    def opener():
        opened.append(FakeConn())
        return opened[-1]

    db = make_session(FakeConn(), FakeConn(), opener=opener)
    db.disconnect()
    assert not db.connected and db.user == "scott"                     # el usuario se recuerda sin conexión
    with pytest.raises(NotConnected):
        db.execute("select a, b from t")
    with pytest.raises(NotConnected):
        db.with_meta_cursor(lambda cur: None)
    db.reconnect()
    assert db.connected and db.conn is opened[0] and db.meta_conn is opened[1]
    assert db.execute("select a, b from t")["rows"]


def test_reconnect_needs_credentials_and_a_free_session():
    with pytest.raises(NotConnected, match="No hay datos"):
        make_session().reconnect()
    db = make_session(opener=FakeConn)
    db.lock.acquire()                                                   # una consulta corriendo
    try:
        with pytest.raises(SessionBusy):
            db.reconnect()
    finally:
        db.lock.release()


@pytest.mark.parametrize("message, lost", [
    ("ORA-03113: end-of-file on communication channel", True),
    ("DPY-4011: the database or network closed the connection", True),
    ("ORA-02396: exceeded maximum idle time, please connect again", True),
    ("ORA-00942: table or view does not exist", False),
])
def test_connection_lost_codes(message, lost):
    assert connection_lost(RuntimeError(message)) is lost


def window_with_connection(tmp_path):
    w = MainWindow(session_path=tmp_path / "sesion", restore=False)
    w.keeper.stop()
    tab = w.add_connection(make_session(FakeConn(), FakeConn(), opener=FakeConn), "DEV", "DEV")
    return w, tab


def test_disconnect_keeps_the_sheets_and_reconnect_resumes(app, tmp_path, monkeypatch):
    w, tab = window_with_connection(tmp_path)
    sheet = tab.current_sheet()
    sheet.editor.setPlainText("select a, b from t")
    assert tab.disconnect()
    assert not tab.connected and "Desconectado" in tab.conn_state.text()
    assert not tab.commit_btn.isEnabled() and not tab.disconnect_btn.isEnabled() and tab.reconnect_btn.isEnabled()
    assert w.tabs.tabText(0) == "○ DEV" and sheet.editor.toPlainText() == "select a, b from t"   # todo sigue

    asked = []
    monkeypatch.setattr(sheet_module.QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.Yes))
    sheet.run()                                                         # sin conexión: ofrece reconectar
    assert asked and "Reconectar y ejecutar" in asked[0]
    # db.connected cambia en el hilo de trabajo; la interfaz se actualiza después (reconnecting vuelve a False)
    assert wait_until(app, lambda: not tab.reconnecting and tab.connected and not sheet.running
                      and sheet.results.model.rowCount() == 2)
    assert w.tabs.tabText(0) == "DEV" and tab.conn_state.text() == "" and tab.commit_btn.isEnabled()
    assert sheet.results.model.rowCount() == 2                          # y ejecutó la consulta


def test_disconnect_asks_about_pending_changes(app, tmp_path, monkeypatch):
    _w, tab = window_with_connection(tmp_path)
    tab.set_pending(True)
    verbs = []
    monkeypatch.setattr(tab, "_resolve_pending", lambda verb: verbs.append(verb) or False)   # "Cancelar"
    assert not tab.disconnect() and tab.connected and verbs == ["desconectar"]
    monkeypatch.setattr(tab, "_resolve_pending", lambda verb: verbs.append(verb) or True)    # Commit o Rollback
    assert tab.disconnect() and not tab.connected and not tab.pending


def test_lost_connection_is_detected(app, tmp_path, monkeypatch):
    _w, tab = window_with_connection(tmp_path)
    tab.set_pending(True)
    warned = []
    monkeypatch.setattr(tab_module.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    sheet = tab.current_sheet()
    run_and_wait(app, sheet, "select perder_conexion from dual")
    assert wait_until(app, lambda: warned)
    assert not tab.connected and "Conexión perdida" in tab.conn_state.text() and not tab.pending
    assert "ORA-03113" in warned[0] and "cambios sin confirmar se perdieron" in warned[0]
    assert "ORA-03113" in sheet.status.text()


def test_metadata_calls_without_connection_fail_fast(app, tmp_path):
    _w, tab = window_with_connection(tmp_path)
    tab.disconnect()
    errors, done = [], threading.Event()
    tab.meta_call(lambda cur: 1, lambda r: done.set(), lambda msg: errors.append(msg) or done.set())
    assert wait_until(app, done.is_set)
    assert "Sin conexión" in errors[0]


def test_failed_reconnect_keeps_it_disconnected(app, tmp_path, monkeypatch):
    _w, tab = window_with_connection(tmp_path)
    tab.disconnect()

    def broken():
        raise RuntimeError("ORA-01017: invalid username/password")
    tab.db.opener = broken
    warned = []
    monkeypatch.setattr(tab_module.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    tab.reconnect()
    assert wait_until(app, lambda: warned)
    assert not tab.connected and "ORA-01017" in warned[0] and "Sin conexión" in tab.conn_state.text()
