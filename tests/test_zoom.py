"""Ctrl+rueda agranda o achica la letra de editores, salidas y resultados (toda la app a la vez)."""
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from minisql.ui.connection_tab import ConnectionTab
from minisql.ui.style import DEFAULT_FONT_SIZE, MAX_FONT_SIZE, MIN_FONT_SIZE, font_zoom

from support import FakeConn, make_session, run_and_wait


def wheel(widget, up=True, ctrl=True):
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120 if up else -120),
                        Qt.NoButton, Qt.ControlModifier if ctrl else Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget.viewport(), event)
    return event


def test_ctrl_wheel_zooms_every_editor_and_the_results(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "DEV")
    sheet = tab.current_sheet()
    run_and_wait(app, sheet, "select a, b from t")
    other = tab.add_sheet()
    grid = sheet.results.view
    grid_size, row_height = grid.font().pointSizeF(), grid.verticalHeader().defaultSectionSize()
    assert sheet.editor.font().pointSize() == DEFAULT_FONT_SIZE

    wheel(sheet.editor)                                                   # Ctrl+rueda arriba en una hoja
    wheel(sheet.editor)
    assert font_zoom.size == DEFAULT_FONT_SIZE + 2
    for editor in (sheet.editor, other.editor):                          # todas las hojas a la vez
        assert editor.font().pointSize() == DEFAULT_FONT_SIZE + 2
    assert sheet.output.font().pointSize() == DEFAULT_FONT_SIZE + 2      # la salida de DBMS_OUTPUT también
    assert grid.font().pointSizeF() == grid_size + 2                     # y la grilla, con filas más altas
    assert grid.verticalHeader().defaultSectionSize() >= row_height

    wheel(grid, up=False)                                                 # desde la grilla también
    assert font_zoom.size == DEFAULT_FONT_SIZE + 1
    assert font_zoom._load()["font_size"] == DEFAULT_FONT_SIZE + 1       # se recuerda para la próxima vez

    font_zoom.reset()                                                     # Ctrl+0
    assert sheet.editor.font().pointSize() == DEFAULT_FONT_SIZE and grid.font().pointSizeF() == grid_size


def test_wheel_without_ctrl_only_scrolls(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "DEV")
    sheet = tab.current_sheet()
    wheel(sheet.editor, ctrl=False)
    assert font_zoom.size == DEFAULT_FONT_SIZE


def test_zoom_limits_and_bad_saved_values(app, monkeypatch):
    font_zoom.set_size(500)
    assert font_zoom.size == MAX_FONT_SIZE
    font_zoom.set_size(1)
    assert font_zoom.size == MIN_FONT_SIZE
    monkeypatch.setattr(font_zoom, "_load", lambda: {"font_size": "grande"})
    monkeypatch.setattr(font_zoom, "_size", None)
    assert font_zoom.size == DEFAULT_FONT_SIZE                            # config.json dañado: tamaño normal


def test_code_editor_gutter_follows_the_zoom(app):
    tab = ConnectionTab(make_session(FakeConn()), "T", "DEV")
    viewer = tab.open_object("SCOTT", "PKG", "PACKAGE")
    code = viewer.pages["Código"]
    width = code.gutter_width()
    font_zoom.set_size(DEFAULT_FONT_SIZE + 6)
    assert code.gutter_width() > width and code.gutter.width() == code.gutter_width()
