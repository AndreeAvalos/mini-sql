"""Apariencia compartida: fuente del editor, colores de estado y la etiqueta de estado."""
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QApplication, QLabel

from ..config import load_settings, save_settings

ERROR_COLOR = "#c0392b"
OK_COLOR = "#27ae60"
WARN_COLOR = "#d35400"
MUTED_COLOR = "gray"


DEFAULT_FONT_SIZE = 11
MIN_FONT_SIZE, MAX_FONT_SIZE = 7, 32


class FontZoom(QObject):
    """Tamaño de letra de editores, salidas y resultados: uno solo para toda la app. Ctrl+rueda lo cambia,
    Ctrl+0 lo vuelve al normal y se recuerda en config.json ("font_size")."""
    changed = Signal(int)

    def __init__(self, load=load_settings, save=save_settings):
        super().__init__()
        self._load, self._save = load, save
        self._size = None

    @property
    def size(self):
        if self._size is None:
            try:
                size = int(self._load().get("font_size", DEFAULT_FONT_SIZE))
            except (TypeError, ValueError, OSError):
                size = DEFAULT_FONT_SIZE
            self._size = min(MAX_FONT_SIZE, max(MIN_FONT_SIZE, size))
        return self._size

    @property
    def offset(self):
        """Puntos de más (o de menos) respecto del tamaño normal: para las fuentes que no son del editor."""
        return self.size - DEFAULT_FONT_SIZE

    def set_size(self, size):
        size = min(MAX_FONT_SIZE, max(MIN_FONT_SIZE, int(size)))
        if size == self.size:
            return
        self._size = size
        try:
            data = self._load()
            data["font_size"] = size
            self._save(data)
        except OSError:
            pass                                    # sin poder guardarlo, igual se aplica en esta sesión
        self.changed.emit(size)

    def step(self, delta):
        self.set_size(self.size + delta)

    def reset(self):
        self.set_size(DEFAULT_FONT_SIZE)

    def wheel(self, event):
        """Para wheelEvent: con Ctrl, agranda o achica y devuelve True (el evento ya se usó)."""
        if not event.modifiers() & Qt.ControlModifier:
            return False
        dy = event.angleDelta().y()
        if dy:
            self.step(1 if dy > 0 else -1)
        event.accept()
        return True


font_zoom = FontZoom()


def mono_font(size=None):
    """Fuente de editores y salidas; sin size, la del zoom actual."""
    font = QFont("Consolas", size or font_zoom.size)
    font.setStyleHint(QFont.Monospace)
    return font


def ui_font(base=None):
    """La fuente normal de la app, con el mismo zoom (para la grilla de resultados)."""
    font = QFont(base or QApplication.font())
    font.setPointSizeF(max(MIN_FONT_SIZE - 2, font.pointSizeF() + font_zoom.offset))
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
        "modified": "#5c4a12", "new": "#1e4a2c", "deleted": "#5a1f1f",     # edición de la grilla
    },
    "light": {
        "header_bg": "#e7eef7", "header_fg": "#1b2a3a", "header_line": "#3b7dd8", "header_sep": "#cbd6e3",
        "rownum_bg": "#f3f3f3", "rownum_fg": "#8a8a8a",
        "base": "#ffffff", "alt": "#f5f8fc", "grid": "#e1e4e8",
        "selection": "#cce4ff", "selection_fg": "#000000",
        "number": "#0451a5", "date": "#8a5a00", "null": "#9a9a9a", "binary": "#a626a4",
        "modified": "#fff1b8", "new": "#d9f7e3", "deleted": "#fbd5d5",     # edición de la grilla
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
