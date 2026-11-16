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


def test_rollback_on_a_dead_session_does_not_block_closing(app, tmp_path, monkeypatch):
    """La sesión murió sin que MiniSQL se enterara: el Rollback falla por la red. No puede quedar en un ciclo
    (no deja cerrar por los cambios, y no se pueden deshacer sin conexión)."""
    _w, tab = window_with_connection(tmp_path)
    tab.set_pending(True)
    warned = []
    monkeypatch.setattr(tab_module.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))

    def dead():
        raise RuntimeError("DPY-4011: the database or network closed the connection")
    monkeypatch.setattr(tab.db, "rollback", dead)
    assert tab.rollback()                                            # resuelto: Oracle ya los deshizo
    assert not tab.connected and not tab.pending and "DPY-4011" in warned[0]
    assert tab.close_connection()                                    # y se puede cerrar


def test_rollback_without_connection_clears_the_pending_changes(app, tmp_path, monkeypatch):
    _w, tab = window_with_connection(tmp_path)
    tab.db.connected = False                                         # ya marcada como desconectada
    tab.set_pending(True)
    warned = []
    monkeypatch.setattr(tab_module.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    assert tab.rollback() and not tab.pending and "ya se perdieron" in warned[0]


def test_failed_rollback_while_closing_offers_a_way_out(app, tmp_path, monkeypatch):
    _w, tab = window_with_connection(tmp_path)
    tab.set_pending(True)
    monkeypatch.setattr(tab, "rollback", lambda: False)             # p. ej. un error de Oracle
    answers = []

    class Box:                                                       # elige "Rollback y cerrar"
        Warning, AcceptRole, DestructiveRole, RejectRole = 0, 0, 1, 2

        def __init__(self, *_a):
            self.buttons = []

        def __getattr__(self, _name):
            return lambda *a, **k: None

        def addButton(self, text, _role):
            self.buttons.append(text)
            return text

        def exec(self):
            return 0

        def clickedButton(self):
            return next(b for b in self.buttons if b.startswith("Rollback"))
    monkeypatch.setattr(tab_module, "QMessageBox", Box)
    monkeypatch.setattr(tab, "_ask", lambda title, text, yes, no: answers.append(yes) or True)
    assert tab._resolve_pending("cerrar")                            # "Cerrar de todas formas"
    assert answers == ["Cerrar de todas formas"] and not tab.pending
