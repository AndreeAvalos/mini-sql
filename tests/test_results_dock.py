"""Resultados abajo o a la derecha: arrastrando la pestaña Resultados o con el botón ⇆/⇅."""
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from minisql.ui.connection_tab import ConnectionTab
from minisql.ui.results_dock import placement

from support import FakeConn, make_session, pump


def sheet_tab(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "DEV")
    tab.resize(1000, 700)
    tab.show()
    pump(app, 3)
    return tab


def mouse(widget, kind, point, buttons=Qt.LeftButton):
    local = QPointF(point)
    glob = QPointF(widget.mapToGlobal(point))
    button = Qt.LeftButton if kind != QEvent.MouseMove else Qt.NoButton
    QApplication.sendEvent(widget, QMouseEvent(kind, local, glob, button, buttons, Qt.NoModifier))


def test_button_moves_results_right_for_every_sheet(app):
    tab = sheet_tab(app)
    first, second = tab.current_sheet(), tab.add_sheet()
    assert placement.position == "bottom" and first.splitter.orientation() == Qt.Vertical
    first.side_btn.click()                                              # ⇆
    for sheet in (first, second):                                       # todas las hojas a la vez
        assert sheet.splitter.orientation() == Qt.Horizontal and sheet.side_btn.text() == "⇅"
    assert placement._load()["results_position"] == "right"             # se recuerda
    later = tab.add_sheet()                                             # y las hojas nuevas nacen así
    assert later.splitter.orientation() == Qt.Horizontal
    second.side_btn.click()                                             # ⇅: de vuelta abajo
    assert first.splitter.orientation() == Qt.Vertical and placement.position == "bottom"


def test_drag_the_results_tab_to_the_right_and_back(app):
    tab = sheet_tab(app)
    sheet = tab.current_sheet()
    bar, splitter = sheet.result_tabs.tabBar(), sheet.splitter
    start = bar.tabRect(0).center()

    def drag_to(target):                                                # target: punto sobre el divisor
        mouse(bar, QEvent.MouseButtonPress, start)
        mouse(bar, QEvent.MouseMove, start + QPoint(40, 0))
        mouse(bar, QEvent.MouseMove, bar.mapFrom(splitter, target))
        preview = sheet.dragger.band.isVisible()
        mouse(bar, QEvent.MouseButtonRelease, bar.mapFrom(splitter, target), Qt.NoButton)
        return preview

    w, h = splitter.width(), splitter.height()
    assert drag_to(QPoint(int(w * 0.9), int(h * 0.3)))                  # se ve dónde va a quedar
    assert splitter.orientation() == Qt.Horizontal and not sheet.dragger.band.isVisible()
    w, h = splitter.width(), splitter.height()
    drag_to(QPoint(int(w * 0.3), int(h * 0.9)))                         # abajo
    assert splitter.orientation() == Qt.Vertical


def test_a_click_still_switches_tabs(app):
    tab = sheet_tab(app)
    sheet = tab.current_sheet()
    bar = sheet.result_tabs.tabBar()
    point = bar.tabRect(1).center()
    mouse(bar, QEvent.MouseButtonPress, point)
    mouse(bar, QEvent.MouseButtonRelease, point, Qt.NoButton)
    assert sheet.result_tabs.currentIndex() == 1 and placement.position == "bottom"


def test_drop_zones(app):
    tab = sheet_tab(app)
    dragger = tab.current_sheet().dragger
    w, h = dragger.splitter.width(), dragger.splitter.height()
    assert dragger.target_at(QPoint(int(w * 0.95), int(h * 0.5))) == "right"
    assert dragger.target_at(QPoint(int(w * 0.5), int(h * 0.95))) == "bottom"
    assert dragger.target_at(QPoint(int(w * 0.3), int(h * 0.3))) is None      # el centro: no cambia nada
