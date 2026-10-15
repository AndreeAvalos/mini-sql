"""Tema visual de la app: paleta (oscura o clara), tipografía y hoja de estilos de los controles.

Se aplica una vez al abrir (`apply_theme`). El modo sale de config.json ("theme": "system", "dark" o "light");
"system" sigue el modo de Windows. Los widgets que pintan colores propios (resaltado de SQL, grilla) preguntan
`is_dark(widget)` o `tokens()`.
"""
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPainter, QPainterPath, QPalette, QPen, QPixmap

from ..config import load_settings

THEMES = {
    "dark": {
        "bg": "#15171c", "surface": "#1c1f26", "surface2": "#242831", "surface3": "#2d323d",
        "border": "#2c313c", "border_strong": "#3a404d",
        "text": "#e3e6ed", "muted": "#8b93a5", "faint": "#5d6577",
        "accent": "#5b8cff", "accent_hover": "#7aa2ff", "accent_text": "#ffffff", "accent_soft": "#25345a",
        "selection": "#2b4372", "success": "#3fb950", "danger": "#f26d6d", "warn": "#e3a83b",
    },
    "light": {
        "bg": "#eef0f4", "surface": "#ffffff", "surface2": "#f5f6f8", "surface3": "#e9ecf1",
        "border": "#dde1e8", "border_strong": "#c9ced8",
        "text": "#1d2129", "muted": "#626b7c", "faint": "#9aa1ad",
        "accent": "#2f6bff", "accent_hover": "#4c80ff", "accent_text": "#ffffff", "accent_soft": "#dde7ff",
        "selection": "#d4e2ff", "success": "#1a7f37", "danger": "#cf222e", "warn": "#9a6700",
    },
}

_mode = None


def resolve_mode(setting=None):
    """"dark" o "light" según el ajuste ("system" sigue a Windows)."""
    setting = setting or load_settings().get("theme", "system")
    if setting in THEMES:
        return setting
    hints = QGuiApplication.styleHints() if QGuiApplication.instance() else None
    scheme = getattr(hints, "colorScheme", lambda: None)() if hints else None
    if scheme == Qt.ColorScheme.Light:
        return "light"
    return "dark"                                  # oscuro por defecto: es lo que más se usa para SQL


def mode():
    return _mode or "dark"


def tokens(name=None):
    """Los colores del tema activo (o de `name`)."""
    return THEMES[name or mode()]


# ---------------------------------------------------------------- tipografía

def _first_family(*names):
    available = set(QFontDatabase.families())
    return next((n for n in names if n in available), names[-1])


def ui_family():
    return _first_family("Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "Sans Serif")


def mono_family():
    return _first_family("Cascadia Mono", "Cascadia Code", "JetBrains Mono", "Consolas", "Monospace")


# ---------------------------------------------------------------- aplicar

def apply_theme(app, setting=None):
    """Estilo Fusion + paleta + fuente + hoja de estilos. Devuelve el modo aplicado ("dark"/"light")."""
    global _mode
    _mode = resolve_mode(setting)
    t = tokens()
    app.setStyle("Fusion")                         # dibuja igual en todas las versiones de Windows
    app.setPalette(build_palette(t))
    app.setFont(QFont(ui_family(), 10))
    app.setStyleSheet(stylesheet(t, _images(t)))
    return _mode


def build_palette(t):
    p = QPalette()
    c = {k: QColor(v) for k, v in t.items()}
    for role, key in ((QPalette.Window, "bg"), (QPalette.WindowText, "text"), (QPalette.Base, "surface"),
                      (QPalette.AlternateBase, "surface2"), (QPalette.Text, "text"), (QPalette.Button, "surface2"),
                      (QPalette.ButtonText, "text"), (QPalette.Highlight, "selection"),
                      (QPalette.HighlightedText, "text"), (QPalette.ToolTipBase, "surface2"),
                      (QPalette.ToolTipText, "text"), (QPalette.PlaceholderText, "faint"), (QPalette.Link, "accent"),
                      (QPalette.Mid, "border"), (QPalette.Midlight, "surface3"), (QPalette.Dark, "border_strong"),
                      (QPalette.Light, "surface3"), (QPalette.Shadow, "bg"), (QPalette.BrightText, "danger")):
        p.setColor(role, c[key])
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c["faint"])
    p.setColor(QPalette.Disabled, QPalette.Base, c["bg"])
    return p


def _stroke_image(name, color, points_list, width=4.5):
    """Un PNG con trazos (la hoja de estilos necesita archivos de imagen para la palomita y la X)."""
    folder = Path(tempfile.gettempdir()) / "minisql-theme"
    folder.mkdir(exist_ok=True)
    path = folder / f"{name}-{color.lstrip('#')}.png"
    if not path.exists():
        pix = QPixmap(32, 32)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for points in points_list:
            stroke = QPainterPath(QPointF(*points[0]))
            for p in points[1:]:
                stroke.lineTo(*p)
            painter.drawPath(stroke)
        painter.end()
        pix.save(str(path))
    return path.as_posix()


def _images(t):
    cross = [[(10, 10), (22, 22)], [(22, 10), (10, 22)]]
    return {
        "check": _stroke_image("check", t["accent_text"], [[(8, 17), (14, 23), (25, 10)]]),
        "close": _stroke_image("close", t["faint"], cross, 3),
        "close_hover": _stroke_image("close", t["text"], cross, 3),
    }


def stylesheet(t, images):
    return f"""
    QToolTip {{
        background: {t['surface2']}; color: {t['text']}; border: 1px solid {t['border_strong']};
        border-radius: 6px; padding: 5px 8px;
    }}

    /* --- botones */
    QPushButton, QToolButton {{
        background: {t['surface2']}; color: {t['text']}; border: 1px solid {t['border']};
        border-radius: 6px; padding: 5px 12px;
    }}
    QPushButton:hover, QToolButton:hover {{ background: {t['surface3']}; border-color: {t['border_strong']}; }}
    QPushButton:pressed, QToolButton:pressed {{ background: {t['border']}; }}
    QPushButton:disabled, QToolButton:disabled {{
        color: {t['faint']}; background: transparent; border-color: {t['border']};
    }}
    QToolButton[autoRaise="true"], QPushButton[variant="ghost"] {{
        background: transparent; border: 1px solid transparent; padding: 4px 8px;
    }}
    QToolButton[autoRaise="true"]:hover, QPushButton[variant="ghost"]:hover {{
        background: {t['surface3']}; border-color: transparent;
    }}
    QToolButton[autoRaise="true"]:disabled, QPushButton[variant="ghost"]:disabled {{
        background: transparent; border-color: transparent;
    }}
    QPushButton[variant="primary"] {{
        background: {t['accent']}; color: {t['accent_text']}; border: 1px solid {t['accent']}; font-weight: 600;
        padding: 5px 16px;
    }}
    QPushButton[variant="primary"]:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
    QPushButton[variant="primary"]:disabled {{
        background: {t['accent_soft']}; color: {t['muted']}; border-color: {t['accent_soft']};
    }}
    QPushButton[variant="success"] {{ color: {t['success']}; font-weight: 600; }}
    QPushButton[variant="danger"] {{ color: {t['danger']}; }}
    QToolButton::menu-indicator {{ image: none; width: 0; }}

    /* --- campos */
    QLineEdit, QComboBox, QSpinBox {{
        background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border']};
        border-radius: 6px; padding: 4px 8px; selection-background-color: {t['selection']};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {t['accent']}; }}
    QLineEdit:disabled, QComboBox:disabled {{ color: {t['faint']}; background: {t['bg']}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 6px;
        selection-background-color: {t['selection']}; outline: none; padding: 4px;
    }}
    QPlainTextEdit, QTextEdit {{
        background: {t['surface']}; border: none; selection-background-color: {t['selection']};
    }}

    /* --- casillas */
    QCheckBox {{ spacing: 7px; }}
    QCheckBox::indicator {{
        width: 15px; height: 15px; border-radius: 4px; border: 1px solid {t['border_strong']};
        background: {t['surface']};
    }}
    QCheckBox::indicator:hover {{ border-color: {t['accent']}; }}
    QCheckBox::indicator:checked {{
        background: {t['accent']}; border-color: {t['accent']}; image: url({images['check']});
    }}

    /* --- pestañas: planas, con subrayado de acento */
    QTabWidget::pane {{ border: none; }}
    QTabBar {{ qproperty-drawBase: 0; }}
    QTabBar::tab {{
        background: transparent; color: {t['muted']}; border: none; border-bottom: 2px solid transparent;
        padding: 7px 14px; margin-right: 2px;
    }}
    QTabBar::tab:hover {{ color: {t['text']}; background: {t['surface3']}; border-top-left-radius: 6px;
        border-top-right-radius: 6px; }}
    QTabBar::tab:selected {{ color: {t['text']}; border-bottom: 2px solid {t['accent']}; font-weight: 600; }}
    QTabBar::close-button {{
        image: url({images['close']}); subcontrol-position: right; border-radius: 4px; margin-left: 4px;
        width: 14px; height: 14px;
    }}
    QTabBar::close-button:hover {{ image: url({images['close_hover']}); background: {t['surface3']}; }}

    /* --- árbol y listas */
    QTreeView, QListView {{
        background: {t['surface']}; border: none; outline: none; show-decoration-selected: 1;
    }}
    QTreeView::item {{ padding: 3px 2px; border-radius: 5px; }}
    QTreeView::item:hover {{ background: {t['surface3']}; }}
    QTreeView::item:selected {{ background: {t['selection']}; color: {t['text']}; }}

    /* --- tarjetas: zonas de trabajo con fondo propio */
    QFrame#card {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; }}
    QFrame#header {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; }}
    QLabel#chip {{
        background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 11px; padding: 2px 10px;
        color: {t['text']};
    }}
    QLabel#muted {{ color: {t['muted']}; }}

    /* --- divisores, barras de desplazamiento y menús */
    QSplitter::handle {{ background: transparent; }}
    QSplitter::handle:horizontal {{ width: 8px; }}
    QSplitter::handle:vertical {{ height: 8px; }}
    QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
    QScrollBar::handle {{ background: {t['border_strong']}; border-radius: 4px; margin: 2px; }}
    QScrollBar::handle:hover {{ background: {t['faint']}; }}
    QScrollBar::handle:vertical {{ min-height: 28px; }}
    QScrollBar::handle:horizontal {{ min-width: 28px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
    QAbstractScrollArea::corner {{ background: transparent; }}
    QMenu {{
        background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 8px; padding: 5px;
    }}
    QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {t['selection']}; }}
    QMenu::item:disabled {{ color: {t['faint']}; }}
    QMenu::separator {{ height: 1px; background: {t['border']}; margin: 5px 8px; }}
    QMenu::indicator {{ width: 14px; height: 14px; margin-left: 6px; }}
    QStatusBar {{ color: {t['muted']}; }}
    QProgressBar {{
        background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 6px; text-align: center;
    }}
    QProgressBar::chunk {{ background: {t['accent']}; border-radius: 5px; }}
    """


# ---------------------------------------------------------------- íconos del explorador

# tipo de objeto → (letra, color): insignias chicas como las de los IDE modernos
OBJECT_BADGES = {
    "TABLE": ("T", "#5b8cff"), "VIEW": ("V", "#2bb5a0"), "MATERIALIZED VIEW": ("M", "#1f9e8a"),
    "PROCEDURE": ("P", "#a371f7"), "FUNCTION": ("F", "#f0883e"), "PACKAGE": ("K", "#db61a2"),
    "PACKAGE BODY": ("K", "#db61a2"), "TYPE": ("Y", "#d4a72c"), "TRIGGER": ("G", "#f26d6d"),
    "SEQUENCE": ("#", "#6e9bd1"), "SYNONYM": ("→", "#8b93a5"), "INDEX": ("I", "#3fb950"),
    "schema": ("S", "#5b8cff"), "column": ("·", "#8b93a5"), "subprogram": ("ƒ", "#a371f7"),
}
_icons = {}


def badge_icon(kind):
    """Ícono de una letra sobre un cuadrito redondeado del color del tipo (None si no hay)."""
    from PySide6.QtGui import QIcon

    if kind not in OBJECT_BADGES:
        return None
    if kind not in _icons:
        letter, color = OBJECT_BADGES[kind]
        size, ratio = 16, 2
        pix = QPixmap(size * ratio, size * ratio)
        pix.setDevicePixelRatio(ratio)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        base = QColor(color)
        fill = QColor(base)
        fill.setAlpha(46)
        painter.setPen(QPen(QColor(base.red(), base.green(), base.blue(), 120), 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), 4, 4)
        font = QFont(ui_family())
        font.setPixelSize(10 if letter not in "·" else 14)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(base.lighter(115) if mode() == "dark" else base.darker(110))
        painter.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, letter)
        painter.end()
        _icons[kind] = QIcon(pix)
    return _icons[kind]
