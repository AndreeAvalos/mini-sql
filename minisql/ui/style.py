"""Apariencia compartida: fuente del editor, colores de estado y la etiqueta de estado."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QLabel

ERROR_COLOR = "#c0392b"
OK_COLOR = "#27ae60"
WARN_COLOR = "#d35400"
MUTED_COLOR = "gray"


def mono_font(size=11):
    font = QFont("Consolas", size)
    font.setStyleHint(QFont.Monospace)
    return font


def is_dark(widget):
    """True si el widget usa un tema oscuro (para elegir colores que se lean bien)."""
    return widget.palette().color(QPalette.Base).lightness() < 128


class StatusLabel(QLabel):
    """Mensaje de estado de una línea; el texto completo queda en el tooltip y se puede seleccionar."""

    def __init__(self, text=""):
        super().__init__(text)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)

    def show_message(self, text, color=""):
        self.setText(text.splitlines()[0] if text else "")
        self.setToolTip(text)
        self.setStyleSheet(f"color: {color};" if color else "")

    def error(self, text):
        self.show_message(text, ERROR_COLOR)


# ---------------------------------------------------------------- tabla de resultados

# Colores de la tabla por tema: encabezado destacado y un color por tipo de dato
RESULT_THEME = {
    "dark": {
        "header_bg": "#2b3a4d", "header_fg": "#e6edf5", "header_line": "#4a90d9", "header_sep": "#3b4b60",
        "rownum_bg": "#252526", "rownum_fg": "#7f7f7f",
        "base": "#1e1e1e", "alt": "#272a2f", "grid": "#363636",
        "selection": "#264f78", "selection_fg": "#ffffff",
        "number": "#9cdcfe", "date": "#e5c07b", "null": "#7f7f7f", "binary": "#c586c0",
    },
    "light": {
        "header_bg": "#e7eef7", "header_fg": "#1b2a3a", "header_line": "#3b7dd8", "header_sep": "#cbd6e3",
        "rownum_bg": "#f3f3f3", "rownum_fg": "#8a8a8a",
        "base": "#ffffff", "alt": "#f5f8fc", "grid": "#e1e4e8",
        "selection": "#cce4ff", "selection_fg": "#000000",
        "number": "#0451a5", "date": "#8a5a00", "null": "#9a9a9a", "binary": "#a626a4",
    },
}


def result_theme(widget):
    return RESULT_THEME["dark" if is_dark(widget) else "light"]


def result_stylesheet(t):
    return f"""
        QTableView {{
            background-color: {t['base']}; alternate-background-color: {t['alt']};
            gridline-color: {t['grid']};
            selection-background-color: {t['selection']}; selection-color: {t['selection_fg']};
        }}
        QHeaderView::section:horizontal {{
            background-color: {t['header_bg']}; color: {t['header_fg']}; font-weight: bold;
            padding: 4px 8px; border: none;
            border-right: 1px solid {t['header_sep']}; border-bottom: 2px solid {t['header_line']};
        }}
        QHeaderView::section:vertical {{
            background-color: {t['rownum_bg']}; color: {t['rownum_fg']};
            padding: 0 6px; border: none; border-right: 1px solid {t['grid']}; border-bottom: 1px solid {t['grid']};
        }}
        QTableCornerButton::section {{
            background-color: {t['header_bg']}; border: none; border-bottom: 2px solid {t['header_line']};
        }}"""
