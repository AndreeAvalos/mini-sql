"""Skeleton: la forma de lo que viene mientras carga (explorador, visor de objetos, resultados, conexiones)."""
from PySide6.QtWidgets import QWidget

from minisql.ui.connecting_tab import ConnectingTab
from minisql.ui.connection_tab import ConnectionTab
from minisql.ui.skeleton import Skeleton, SkeletonOverlay

from support import FakeConn, make_session, pump, wait_until


def test_every_kind_paints(app):
    for kind in ("line", "tree", "table", "text", "window"):
        sk = Skeleton(kind)
        sk.resize(600, 300)
        sk.show()
        sk._advance()                                     # el brillo avanza
        assert not sk.grab().isNull()
        assert sk.timer.isActive()
        sk.hide()
        assert not sk.timer.isActive()                    # oculto no gasta CPU


def test_overlay_waits_before_appearing(app):
    host = QWidget()
    host.resize(400, 200)
    host.show()
    sk = SkeletonOverlay(host)
    sk.start(delay=50)
    assert sk.active and not sk.isVisible()               # una carga rápida no llega a mostrarlo
    wait_until(app, sk.isVisible)
    assert sk.geometry() == host.rect()
    host.resize(500, 250)
    pump(app)
    assert sk.geometry() == host.rect()                   # sigue al padre
    sk.stop()
    assert not sk.active and not sk.isVisible()


def test_explorer_shows_skeleton_rows_while_loading(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    tree = tab.explorer.tree
    pump(app)
    tables = tree.topLevelItem(0).child(0)
    tab.explorer.start_loading(tables)
    assert tables.childCount() == 3
    assert all(isinstance(tree.itemWidget(tables.child(i), 0), Skeleton) for i in range(3))
    tab.explorer.refresh_item(tables)                     # carga de verdad: los renglones fantasma se van
    pump(app)
    assert tables.text(0) == "Tablas (2)" and tree.itemWidget(tables.child(0), 0) is None


def test_first_run_shows_a_table_skeleton(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    sheet = tab.current_sheet()
    panel = sheet.results
    for before in (None, "message", "table"):                      # primera vez, tras un error, tras una tabla
        if before == "message":
            panel.show_message("ORA-00942: la tabla no existe", error=True)
        elif before == "table":
            panel.show_result({"headers": ["A"], "rows": [(1,)]})
        sheet.set_running(True)
        assert panel.skeleton.active and not panel.overlay.dim and not panel.view.isEnabled()
        sheet.set_running(False)
        assert not panel.skeleton.active
    panel.set_busy("Guardando…")                                     # guardar: la tabla queda a la vista, apagada
    assert not panel.skeleton.active and panel.overlay.dim
    panel.set_busy(None)


def test_object_viewer_tabs_use_skeletons(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    viewer = tab.open_object("SCOTT", "EMP", "TABLE")
    assert viewer.skeletons["Columnas"].active                     # cargando
    wait_until(app, lambda: viewer.pages["Columnas"].model() is not None
               and viewer.pages["Columnas"].model().rowCount() == 2)
    assert not viewer.skeletons["Columnas"].active
    assert viewer.skeletons["DDL"].kind == "text" and "Datos" not in viewer.skeletons


def test_connecting_tab_keeps_the_saved_state(app):
    state = {"name": "DEV", "tabs": [{"kind": "sheet", "title": "Hoja1", "text": "x"}]}
    holder = ConnectingTab(state)
    assert holder.snapshot() is state and holder.close_connection()
    assert "Conectando a <b>DEV</b>" in holder.label.text() and "1 hoja(s)" in holder.label.text()
    holder.show_waiting()
    assert "contraseña" in holder.label.text()
