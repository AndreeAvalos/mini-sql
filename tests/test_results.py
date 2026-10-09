"""Tabla de resultados: colores por tipo, formato de valores y ordenar con clic en el encabezado."""
import datetime
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from minisql.ui.results import ResultModel, ResultView, display_text, kind_of
from minisql.ui.style import RESULT_THEME

ROWS = [
    (3, "Carla", Decimal("150.75"), datetime.datetime(2024, 1, 5)),
    (1, "Ana", None, datetime.datetime(2023, 7, 9, 14, 30, 0)),
    (2, "beto", Decimal("99.5"), None),
]
HEADERS = ["ID", "NOMBRE", "SALDO", "FECHA"]


def test_value_kinds_and_display():
    assert [kind_of(v) for v in (None, 3, Decimal("1.5"), 2.5, True, "x", b"ab")] == \
        ["null", "number", "number", "number", "text", "text", "binary"]
    assert kind_of(datetime.date(2024, 1, 1)) == kind_of(datetime.datetime(2024, 1, 1)) == "date"
    assert display_text(datetime.datetime(2024, 1, 5)) == "2024-01-05"                  # DATE sin hora
    assert display_text(datetime.datetime(2023, 7, 9, 14, 30)) == "2023-07-09 14:30:00"
    assert display_text(None) == "(null)" and display_text(b"abc") == "<3 bytes>"
    assert display_text("x" * 500).endswith("…")


def test_colors_alignment_and_header():
    model = ResultModel(HEADERS, ROWS, RESULT_THEME["dark"])
    cell = model.index
    assert model.data(cell(0, 2), Qt.TextAlignmentRole) == Qt.AlignRight | Qt.AlignVCenter    # Decimal a la derecha
    assert model.data(cell(0, 2), Qt.ForegroundRole).name() == RESULT_THEME["dark"]["number"]
    assert model.data(cell(0, 3), Qt.ForegroundRole).name() == RESULT_THEME["dark"]["date"]
    assert model.data(cell(1, 2), Qt.FontRole).italic()                                     # (null) en cursiva
    assert model.data(cell(0, 1), Qt.ForegroundRole) is None                                # texto: color normal
    assert model.headerData(1, Qt.Horizontal) == "NOMBRE"
    assert "ordenar" in model.headerData(1, Qt.Horizontal, Qt.ToolTipRole)
    assert model.headerData(0, Qt.Vertical) == 1


def test_sort_keeps_nulls_last_and_handles_mixed_types():
    model = ResultModel(HEADERS, ROWS)
    model.sort(2, Qt.AscendingOrder)
    assert [r[0] for r in model.rows] == [2, 3, 1]          # 99.5, 150.75, null
    model.sort(2, Qt.DescendingOrder)
    assert [r[0] for r in model.rows] == [3, 2, 1]          # el null sigue al final
    model.sort(1, Qt.AscendingOrder)
    assert [r[1] for r in model.rows] == ["Ana", "Carla", "beto"]
    mixed = ResultModel(["X"], [("b",), (2,), (None,), ("a",), (1,)])
    mixed.sort(0)                                           # no falla al mezclar números y texto
    assert mixed.rows[-1] == (None,)
    model.sort(-1)                                          # sin columna: no hace nada
    assert len(model.rows) == 3


def test_results_arrive_in_query_order_and_sort_on_click(app):
    view = ResultView()
    model = view.show_rows(HEADERS, ROWS)
    assert [r[0] for r in model.rows] == [3, 1, 2]                  # el ORDER BY de la consulta se respeta
    assert view.horizontalHeader().sortIndicatorSection() == -1
    view.sortByColumn(0, Qt.AscendingOrder)                         # lo mismo que hacer clic en ID
    assert [r[0] for r in view.model().rows] == [1, 2, 3]
    view.selectRow(0)
    view.copy_selection()
    assert QGuiApplication.clipboard().text().startswith("1\tAna")   # se copia lo que se ve, ya ordenado
    model = view.show_rows(HEADERS, ROWS)                            # un resultado nuevo: sin reordenar
    assert [r[0] for r in model.rows] == [3, 1, 2]
    assert view.horizontalHeader().sortIndicatorSection() == -1


def test_header_is_styled(app):
    view = ResultView()
    style = view.styleSheet()
    assert "QHeaderView::section:horizontal" in style and "font-weight: bold" in style
    assert view.theme["header_line"] in style
