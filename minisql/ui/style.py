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
