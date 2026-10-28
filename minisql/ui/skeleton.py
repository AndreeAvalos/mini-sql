"""Skeleton: la forma del contenido que viene (barras grises con un brillo que las recorre) mientras se carga.

- `Skeleton(kind)`: widget que se pone en un layout o como ítem del árbol ("line").
- `SkeletonOverlay(parent, kind)`: tapa a su padre mientras carga; `start()` / `stop()`.

Tipos: "line" (un renglón del árbol), "tree", "table", "text" (código/DDL) y "window" (una conexión completa:
explorador, editor y resultados). Solo aparece si la carga dura más de `DELAY_MS`, para no parpadear.
"""
import random

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QWidget

from .theme import tokens

DELAY_MS = 200          # cargas más rápidas no muestran nada
FRAME_MS = 33           # ~30 cuadros por segundo
CYCLE_MS = 1300         # una pasada del brillo
ROW_H = 22              # alto de un renglón de tabla/árbol


class Skeleton(QWidget):
    def __init__(self, kind="table", parent=None, rows=None, seed=None):
        super().__init__(parent)
        self.kind, self.rows = kind, rows
        rnd = random.Random(seed if seed is not None else id(self))
        self.widths = [rnd.uniform(0.35, 0.9) for _ in range(64)]    # anchos variados, fijos para este widget
        self.phase = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(FRAME_MS)
        self.timer.timeout.connect(self._advance)
        self.setAttribute(Qt.WA_OpaquePaintEvent, kind not in ("line",))
        if kind == "line":
            self.setFixedHeight(ROW_H - 4)

    # --- animación
    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.timer.stop()

    def _advance(self):
        self.phase = (self.phase + FRAME_MS / CYCLE_MS) % 1.0
        self.update()

    # --- dibujo
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = tokens()
        if self.kind != "line":
            p.fillRect(self.rect(), QColor(c["surface"]))
        base, shine = QColor(c["surface3"]), QColor(c["border_strong"])
        # una franja más clara que avanza de izquierda a derecha; fuera de ella, el color base (Pad)
        width = max(1, self.width())
        x = (self.phase * 1.6 - 0.3) * width
        grad = QLinearGradient(x - width * 0.25, 0, x + width * 0.25, 0)
        grad.setColorAt(0, base)
        grad.setColorAt(0.5, shine)
        grad.setColorAt(1, base)
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        getattr(self, f"_paint_{self.kind}", self._paint_table)(p)
        p.end()

    def _bar(self, p, x, y, w, h, radius=4):
        if w > 2 and h > 2:
            p.drawRoundedRect(QRectF(x, y, w, h), radius, radius)

    def _paint_line(self, p):
        h = self.height()
        self._bar(p, 2, (h - 12) / 2, 12, 12, 3)                       # el ícono
        self._bar(p, 22, (h - 10) / 2, (self.width() - 30) * self.widths[0] * 0.8, 10)

    def _paint_tree(self, p, rect=None):
        r = rect or QRectF(self.rect())
        y, i = r.top() + 8, 0
        while y + ROW_H <= r.bottom() and (self.rows is None or i < self.rows):
            indent = 0 if i % 4 == 0 else 16
            self._bar(p, r.left() + 8 + indent, y + 4, 12, 12, 3)
            self._bar(p, r.left() + 28 + indent, y + 5, (r.width() - 44 - indent) * self.widths[i % 64], 10)
            y, i = y + ROW_H, i + 1

    def _paint_table(self, p, rect=None):
        r = rect or QRectF(self.rect())
        cols = max(2, min(6, int(r.width() // 140)))
        col_w = (r.width() - 16) / cols
        y = r.top() + 8
        for j in range(cols):                                           # encabezados, más marcados
            self._bar(p, r.left() + 8 + j * col_w + 6, y + 4, (col_w - 16) * 0.6, 12)
        y, i = y + ROW_H + 6, 0
        while y + ROW_H <= r.bottom() and (self.rows is None or i < self.rows):
            for j in range(cols):
                w = (col_w - 16) * self.widths[(i * cols + j) % 64]
                self._bar(p, r.left() + 8 + j * col_w + 6, y + 6, w, 9)
            y, i = y + ROW_H, i + 1

    def _paint_text(self, p, rect=None):
        r = rect or QRectF(self.rect())
        y, i = r.top() + 10, 0
        while y + 18 <= r.bottom() and (self.rows is None or i < self.rows):
            indent = (0, 0, 24, 24, 48, 24)[i % 6]
            if i % 7 != 6:                                              # alguna línea en blanco, como el código
                self._bar(p, r.left() + 12 + indent, y + 4, (r.width() - 60 - indent) * self.widths[i % 64], 9)
            y, i = y + 18, i + 1

    def _paint_window(self, p):
        r = QRectF(self.rect())
        c = tokens()
        side = min(260.0, r.width() * 0.25)
        p.save()
        p.setBrush(QColor(c["border"]))
        p.drawRect(QRectF(side, r.top(), 1, r.height()))                # divisiones del explorador y del editor
        mid = r.top() + r.height() * 0.45
        p.drawRect(QRectF(side, mid, r.width() - side, 1))
        p.restore()
        self._bar(p, 10, r.top() + 10, side - 20, 22, 6)               # buscador del explorador
        self._paint_tree(p, QRectF(0, r.top() + 36, side, r.height() - 36))
        self._paint_text(p, QRectF(side, r.top(), r.width() - side, mid - r.top()))
        self._paint_table(p, QRectF(side, mid + 1, r.width() - side, r.bottom() - mid - 1))


class SkeletonOverlay(Skeleton):
    """Tapa a su padre (la tabla, el DDL…) mientras carga y lo sigue al cambiar de tamaño."""

    def __init__(self, parent, kind="table"):
        super().__init__(kind, parent)
        self.delay = QTimer(self)
        self.delay.setSingleShot(True)
        self.delay.timeout.connect(self._appear)
        parent.installEventFilter(self)
        self.hide()

    @property
    def active(self):
        return self.delay.isActive() or self.isVisible()

    def start(self, delay=DELAY_MS):
        if delay:
            self.delay.start(delay)
        else:
            self._appear()

    def stop(self):
        self.delay.stop()
        self.hide()

    def _appear(self):
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(obj.rect())
        return False

    def mousePressEvent(self, event):
        event.accept()

    mouseDoubleClickEvent = mousePressEvent

    def wheelEvent(self, event):
        event.accept()
