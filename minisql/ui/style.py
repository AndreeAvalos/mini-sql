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
    from .theme import mono_family

    font = QFont(mono_family(), size or font_zoom.size)
    font.setStyleHint(QFont.Monospace)
    return font


def ui_font(base=None):
    """La fuente normal de la app, con el mismo zoom (para la grilla de resultados)."""
    font = QFont(base or QApplication.font())
    font.setPointSizeF(max(MIN_FONT_SIZE - 2, font.pointSizeF() + font_zoom.offset))
    return font


def card(widget, margins=6):
    """Envuelve widget en una "tarjeta": fondo propio, borde suave y esquinas redondeadas (ver theme.py)."""
    from PySide6.QtWidgets import QFrame, QVBoxLayout

    frame = QFrame()
    frame.setObjectName("card")
    box = QVBoxLayout(frame)
    box.setContentsMargins(margins, margins, margins, margins)
    box.setSpacing(0)
    box.addWidget(widget)
    frame.setVisible(not widget.isHidden())
    frame.follower = _FollowVisibility(frame, widget)     # si el contenido se oculta, la tarjeta también
    return frame


class _FollowVisibility(QObject):
    def __init__(self, frame, widget):
        super().__init__(frame)
        self.frame = frame
        widget.installEventFilter(self)

    def eventFilter(self, _obj, event):
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.HideToParent:
            self.frame.hide()
        elif event.type() == QEvent.ShowToParent:
            self.frame.show()
        return False


def variant(button, name):
    """Estilo del botón: "primary" (acción principal), "ghost" (discreto), "success", "danger"."""
    button.setProperty("variant", name)
    return button


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
        "header_bg": "#242831", "header_fg": "#c9cfdb", "header_line": "#3a404d", "header_sep": "#2c313c",
        "rownum_bg": "#1c1f26", "rownum_fg": "#5d6577",
        "base": "#1c1f26", "alt": "#20242c", "grid": "#2a2f39",
        "selection": "#2b4372", "selection_fg": "#ffffff",
        "number": "#7cc4ff", "date": "#e3b45b", "null": "#5d6577", "binary": "#c792ea",
        "modified": "#4d3f14", "new": "#183d2a", "deleted": "#4d1d22",     # edición de la grilla
    },
    "light": {
        "header_bg": "#f5f6f8", "header_fg": "#3b4250", "header_line": "#c9ced8", "header_sep": "#dde1e8",
        "rownum_bg": "#fafbfc", "rownum_fg": "#9aa1ad",
        "base": "#ffffff", "alt": "#f8f9fb", "grid": "#eceef2",
        "selection": "#d4e2ff", "selection_fg": "#000000",
        "number": "#0b5cad", "date": "#8a5a00", "null": "#9aa1ad", "binary": "#a626a4",
        "modified": "#fff1b8", "new": "#dcf5e3", "deleted": "#fbd9d9",     # edición de la grilla
    },
}


def result_theme(widget):
    return RESULT_THEME["dark" if is_dark(widget) else "light"]


def result_stylesheet(t):
    return f"""
        QTableView {{
            background-color: {t['base']}; alternate-background-color: {t['alt']};
            gridline-color: {t['grid']}; border: none;
            selection-background-color: {t['selection']}; selection-color: {t['selection_fg']};
        }}
        QHeaderView {{ background-color: {t['header_bg']}; }}
        QHeaderView::section:horizontal {{
            background-color: {t['header_bg']}; color: {t['header_fg']}; font-weight: 600;
            padding: 6px 10px; border: none;
            border-right: 1px solid {t['header_sep']}; border-bottom: 1px solid {t['header_line']};
        }}
        QHeaderView::section:horizontal:hover {{ color: {t['selection_fg']}; }}
        QHeaderView::section:vertical {{
            background-color: {t['rownum_bg']}; color: {t['rownum_fg']};
            padding: 0 8px; border: none; border-right: 1px solid {t['grid']};
        }}
        QTableCornerButton::section {{
            background-color: {t['header_bg']}; border: none; border-bottom: 1px solid {t['header_line']};
        }}"""
