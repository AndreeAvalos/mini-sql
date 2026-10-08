"""Guardián de sesión y cierre de la aplicación."""

import json
import subprocess
import sys
from pathlib import Path

from minisql.ui import main_window
from minisql.ui.connection_tab import ConnectionTab
from minisql.ui.main_window import MainWindow
from minisql.workspace import WorkspaceStore, load_session, read_json, write_json_atomic

from support import FakeConn, FakeDialog, SlowConn, make_session, start_slow_query, wait_until


def test_json_is_atomic_with_backup(tmp_path):
    path = tmp_path / "indice.json"
    write_json_atomic(path, {"v": 1})
    write_json_atomic(path, {"v": 2})
    assert read_json(path) == {"v": 2}
    assert json.loads(path.with_suffix(".bak").read_text(encoding="utf-8")) == {"v": 1}
    path.write_text("{roto", encoding="utf-8")            # cierre inesperado a media escritura
    assert read_json(path) == {"v": 1}
    assert read_json(tmp_path / "no-existe.json") is None


def sheet(title, text):
    return {"kind": "sheet", "title": title, "text": text, "cursor": 0}


def test_workspace_is_one_folder_per_connection_and_one_file_per_sheet(tmp_path):
    root = tmp_path / "sesion"
    big = "select 1 from dual;\n" * 50000                 # ~1 MB
    data = {"connections": [
        {"name": "DEV", "user": "u", "dsn": "DEV", "current": 0,
         "tabs": [sheet("Hoja1", big), sheet("Cierre: mes/año?", "select 2"), sheet("hoja1", "select 3"),
                  {"kind": "object", "owner": "SCOTT", "name": "PKG", "type": "PACKAGE", "code": "CREATE …"},
                  {"kind": "object", "owner": "SCOTT", "name": "EMP", "type": "TABLE", "code": None}]},
        {"name": "PROD", "user": "u", "dsn": "PROD", "current": 0, "tabs": [sheet("Hoja1", "select 4")]}],
        "parked": [{"name": "DEV", "user": "u", "dsn": "DEV", "current": 0, "tabs": [sheet("Vieja", "select 5")]}]}
    store = WorkspaceStore(root)
    store.save(data)
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.sql"))
    assert files == ["DEV (guardadas)/Vieja.sql", "DEV/Cierre_ mes_año_.sql", "DEV/Hoja1.sql", "DEV/hoja1 (2).sql",
                     "DEV/objetos/SCOTT.PKG.sql", "PROD/Hoja1.sql"]
    assert (root / "DEV/Hoja1.sql").read_text(encoding="utf-8") == big
    index = (root / "indice.json").read_text(encoding="utf-8")
    assert "select" not in index and len(index) < 3000      # el índice no lleva el texto de las hojas
    assert load_session(root) == data | {"version": 2}       # se recupera todo, con los nombres reales

    # solo se reescribe lo que cambió
    stamp = (root / "DEV/Hoja1.sql").stat().st_mtime_ns
    data["connections"][1]["tabs"][0]["text"] = "select 44"
    store.save(data)
    assert (root / "DEV/Hoja1.sql").stat().st_mtime_ns == stamp
    assert (root / "PROD/Hoja1.sql").read_text(encoding="utf-8") == "select 44"

    # cerrar o renombrar una hoja borra su archivo viejo; las carpetas vacías desaparecen
    data["connections"][1]["tabs"] = []
    data["connections"][0]["tabs"][1]["title"] = "Cierre"
    data["parked"] = []
    store.save(data)
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.sql"))
    assert files == ["DEV/Cierre.sql", "DEV/Hoja1.sql", "DEV/hoja1 (2).sql", "DEV/objetos/SCOTT.PKG.sql"]
    assert not (root / "PROD").exists() and not (root / "DEV (guardadas)").exists()


def test_workspace_migrates_old_single_json(tmp_path):
    legacy = tmp_path / "sesion.json"
    dev = {"name": "DEV", "user": "u", "dsn": "DEV", "tabs": [sheet("Hoja1", "select 1")]}
    old = {"version": 1, "connections": [dev],
           "parked": []}
    write_json_atomic(legacy, old)
    store = WorkspaceStore(tmp_path / "sesion", legacy_file=legacy)
    data = store.load()
    assert data["connections"][0]["tabs"][0]["text"] == "select 1"
    store.save(data)
    assert (tmp_path / "sesion/DEV/Hoja1.sql").read_text(encoding="utf-8") == "select 1"
    assert WorkspaceStore(tmp_path / "sesion", legacy_file=legacy).load()["version"] == 2


def test_connection_snapshot_and_restore(app):
    tab = ConnectionTab(make_session(FakeConn()), "DEV", "DEV_ALIAS")
    tab.current_sheet().editor.setPlainText("select 1 from dual")
    tab.sheets.setTabText(0, "Ventas")
    tab.add_sheet(text="select 2 from dual")
    viewer = tab.open_object("SCOTT", "PKG", "PACKAGE")
    code = viewer.pages["Código"]
    assert wait_until(app, lambda: "PACKAGE BODY" in code.toPlainText())
    code.selectAll()
    code.insertPlainText("CREATE OR REPLACE PROCEDURE PKG IS BEGIN NULL; END;")
    state = tab.snapshot()
    assert state["name"] == "DEV" and state["dsn"] == "DEV_ALIAS" and state["current"] == 2
    assert [t.get("title") for t in state["tabs"]] == ["Ventas", "Hoja2", None]
    assert state["tabs"][2]["code"].startswith("CREATE OR REPLACE PROCEDURE")

    other = ConnectionTab(make_session(FakeConn()), "DEV", "DEV_ALIAS")
    other.restore(state)
    titles = [other.sheets.tabText(i) for i in range(other.sheets.count())]
    assert titles == ["Ventas", "Hoja2", "◆ PKG"]                 # sin la hoja vacía inicial
    assert other.sheets.widget(0).editor.toPlainText() == "select 1 from dual"
    assert other.sheets.widget(0).editor.document().isModified()  # pedirá confirmar al cerrarla
    restored = other.sheets.widget(2)
    assert wait_until(app, lambda: restored.pages["Código"].toPlainText().startswith("CREATE OR REPLACE PROCEDURE"))
    assert restored.has_unsaved() and "recuperados" in restored.status.text()
    assert other.add_sheet().windowTitle() == "" and other.sheets.tabText(3) == "Hoja3"


def test_keeper_autosaves_and_keeps_closed_connections(app, tmp_path):
    path = tmp_path / "sesion"
    w = MainWindow(session_path=path, restore=False)
    tab = w.add_connection(make_session(), "DEV", "DEV")
    tab.current_sheet().editor.setPlainText("select * from emp")
    w.keeper.save()
    saved = load_session(path)
    assert saved["connections"][0]["tabs"][0]["text"] == "select * from emp"
    assert "password" not in json.dumps(saved).lower()
    # cerrar la conexión no pierde sus hojas
    w.close_conn(0)
    saved = load_session(path)
    assert saved["connections"] == [] and saved["parked"][0]["name"] == "DEV"
    # al volver a conectarse a ese perfil, regresan
    tab = w.add_connection(make_session(), "DEV", "DEV")
    assert tab.current_sheet().editor.toPlainText() == "select * from emp"
    assert w.keeper.parked == {} and load_session(path)["parked"] == []
    w.keeper.stop()


def test_restore_session_on_startup(app, tmp_path, monkeypatch):
    path = tmp_path / "sesion"
    sheets = [{"kind": "sheet", "title": "Cierre", "text": "update t set x = 1 where id = 2", "cursor": 3}]
    WorkspaceStore(path).save({"connections": [{"name": "PROD", "user": "u", "dsn": "PROD", "tabs": sheets}],
                               "parked": []})
    monkeypatch.setattr(main_window, "ConnectDialog", FakeDialog)
    # si cancela la reconexión, las hojas quedan guardadas (no se pierden)
    FakeDialog.accept = False
    w = MainWindow(session_path=path, restore=False)
    w.restore_session()
    assert w.tabs.count() == 0 and "PROD" in w.keeper.parked
    assert load_session(path)["parked"][0]["tabs"][0]["title"] == "Cierre"
    w.keeper.stop()
    # al abrir otra vez no insiste; al conectarse a PROD, vuelve todo
    FakeDialog.accept = True
    w2 = MainWindow(session_path=path, restore=False)
    w2.restore_session()
    assert w2.tabs.count() == 1
    tab = w2.tabs.widget(0)
    assert tab.sheets.tabText(0) == "Cierre"
    assert tab.current_sheet().editor.toPlainText() == "update t set x = 1 where id = 2"
    assert w2.keeper.parked == {}
    w2.keeper.stop()


def test_close_window_saves_full_session(app, tmp_path):
    path = tmp_path / "sesion"
    w = MainWindow(session_path=path, restore=False)
    tab = w.add_connection(make_session(), "DEV", "DEV")
    tab.current_sheet().editor.setPlainText("select sysdate from dual")
    w.close()
    saved = load_session(path)
    assert saved["connections"][0]["name"] == "DEV"
    assert saved["connections"][0]["tabs"][0]["text"] == "select sysdate from dual"
    assert saved["parked"] == []


CRASH_SCRIPT = r"""
import os, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path[:0] = [sys.argv[2], sys.argv[3]]          # el proyecto y tests/
from PySide6.QtWidgets import QApplication
app = QApplication([])
from minisql.ui.main_window import MainWindow
from support import make_session
w = MainWindow(session_path=sys.argv[1], restore=False)
w.keeper.timer.setInterval(200)
tab = w.add_connection(make_session(), "DEV", "DEV")
tab.current_sheet().editor.setPlainText("select 'trabajo sin guardar' from dual")
end = time.time() + 1.5
while time.time() < end:
    app.processEvents()
    time.sleep(0.02)
os._exit(1)          # cierre inesperado: sin closeEvent ni limpieza
"""


def test_survives_unexpected_close(tmp_path):
    path = tmp_path / "sesion"
    script = tmp_path / "crash.py"
    script.write_text(CRASH_SCRIPT, encoding="utf-8")
    tests = Path(__file__).resolve().parent
    proc = subprocess.run([sys.executable, str(script), str(path), str(tests.parent), str(tests)],
                          cwd=str(tests.parent), timeout=60)
    assert proc.returncode == 1
    saved = load_session(path)
    assert saved["connections"][0]["tabs"][0]["text"] == "select 'trabajo sin guardar' from dual"


def test_close_window_cancels_running_query(app, tmp_path, monkeypatch):
    monkeypatch.setattr(ConnectionTab, "_ask", lambda self, *a: True)
    conn = SlowConn()
    w = MainWindow(session_path=tmp_path / "s", restore=False)
    w.show()
    _tab, _sheet = start_slow_query(app, w, conn)
    w.close()
    assert conn.cancelled and not w.isVisible()
    assert load_session(tmp_path / "s")["connections"][0]["tabs"][0]["text"] == "select * from tabla_lenta"


def test_close_window_can_keep_waiting(app, tmp_path, monkeypatch):
    monkeypatch.setattr(ConnectionTab, "_ask", lambda self, *a: False)
    conn = SlowConn()
    w = MainWindow(session_path=tmp_path / "s", restore=False)
    w.show()
    _tab, sheet = start_slow_query(app, w, conn)
    w.close()
    assert w.isVisible() and not conn.cancelled and sheet.running
    conn.release.set()
    assert wait_until(app, lambda: not sheet.running)
    w.keeper.stop()


def test_close_window_when_oracle_does_not_respond(app, tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(ConnectionTab, "_ask", lambda self, title, *a: asked.append(title) or True)
    monkeypatch.setattr(ConnectionTab, "CLOSE_WAIT_S", 0.3)
    conn = SlowConn(responds=False)
    w = MainWindow(session_path=tmp_path / "s", restore=False)
    w.show()
    _tab, _sheet = start_slow_query(app, w, conn)
    w.close()
    assert asked == ["En ejecución", "Sin respuesta"] and not w.isVisible()
    conn.release.set()                           # que el hilo de la prueba termine


def test_close_sheet_cancels_running_query(app, monkeypatch):
    monkeypatch.setattr(ConnectionTab, "_ask", lambda self, *a: True)
    conn = SlowConn()
    tab = ConnectionTab(make_session(conn), "DEV", "DEV")
    sheet = tab.current_sheet()
    tab.add_sheet()
    sheet.editor.setPlainText("select * from tabla_lenta")
    sheet.run()
    assert wait_until(app, lambda: tab.db.busy)
    tab.close_sheet(tab.sheets.indexOf(sheet))
    assert conn.cancelled and tab.sheets.indexOf(sheet) == -1
