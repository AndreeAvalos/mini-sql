"""Dónde van los resultados de las hojas: abajo del editor o a la derecha.

Se cambia arrastrando la pestaña "Resultados" (o "DBMS_OUTPUT") hacia la derecha o hacia abajo, o con el botón
⇆/⇅. Es una sola preferencia para todas las hojas y se recuerda en config.json ("results_position").
"""
from PySide6.QtCore import QEvent, QObject, QRect, Qt, Signal
from PySide6.QtWidgets import QApplication, QFrame

from ..config import load_settings, save_settings

POSITIONS = ("bottom", "right")


class ResultsPlacement(QObject):
    changed = Signal(str)

    def __init__(self, load=load_settings, save=save_settings):
        super().__init__()
        self._load, self._save = load, save
        self._position = None

    @property
    def position(self):
        if self._position is None:
            try:
                value = self._load().get("results_position", "bottom")
            except OSError:
                value = "bottom"
            self._position = value if value in POSITIONS else "bottom"
        return self._position

    def set(self, position):
        if position not in POSITIONS or position == self.position:
            return
        self._position = position
        try:
            data = self._load()
            data["results_position"] = position
            self._save(data)
        except OSError:
            pass                                   # sin poder guardarlo, igual se aplica en esta sesión
        self.changed.emit(position)

    def toggle(self):
        self.set("right" if self.position == "bottom" else "bottom")


placement = ResultsPlacement()


class ResultsDragger(QObject):
    """Arrastrar las pestañas de resultados (tab_bar) sobre el divisor (splitter) para cambiarlas de lado.
    Un clic normal sigue cambiando de pestaña: el arrastre empieza recién al moverse un poco."""

    def __init__(self, tab_bar, splitter):
        super().__init__(tab_bar)
        self.tab_bar, self.splitter = tab_bar, splitter
        self.start = None
        self.dragging = False
        self.target = None
        self._band = None
        tab_bar.installEventFilter(self)

    @property
    def band(self):
        """Dónde van a quedar: un recuadro del color de acento. Se crea al primer arrastre (cuando el divisor
        ya está dentro de la hoja) y va sobre su contenedor, no dentro: QSplitter convierte en un panel más a
        cualquier widget hijo."""
        if self._band is None:
            self._band = QFrame(self.splitter.parentWidget())
            self._band.setObjectName("dropZone")
            self._band.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._band.hide()
        return self._band

    def eventFilter(self, _obj, event):
        kind = event.type()
        if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self.start, self.dragging = event.globalPosition().toPoint(), False
        elif kind == QEvent.MouseMove and self.start is not None and event.buttons() & Qt.LeftButton:
            pos = event.globalPosition().toPoint()
            if not self.dragging and (pos - self.start).manhattanLength() > QApplication.startDragDistance() * 3:
                self.dragging = True
                self.tab_bar.setCursor(Qt.ClosedHandCursor)
            if self.dragging:
                self._preview(self.splitter.mapFromGlobal(pos))
                return True
        elif kind == QEvent.MouseButtonRelease and self.start is not None:
            was_dragging, target = self.dragging, self.target
            self._reset()
            if was_dragging:
                if target:
                    placement.set(target)
                return True
        return False

    def target_at(self, point):
        """"right", "bottom" o None según dónde está el cursor sobre la hoja."""
        w, h = max(1, self.splitter.width()), max(1, self.splitter.height())
        rx, ry = point.x() / w, point.y() / h
        if rx > 0.6 and rx - 0.6 >= ry - 0.6:
            return "right"
        if ry > 0.6:
            return "bottom"
        return None

    def _preview(self, point):
        self.target = self.target_at(point)
        area = self.splitter.geometry()
        if self.target == "right":
            self.band.setGeometry(QRect(area.center().x(), area.top(), area.width() // 2, area.height()))
        elif self.target == "bottom":
            self.band.setGeometry(QRect(area.left(), area.center().y(), area.width(), area.height() // 2))
        if self.target:
            self.band.show()
            self.band.raise_()
        else:
            self.band.hide()

    def _reset(self):
        self.start, self.dragging, self.target = None, False, None
        self.band.hide()
        self.tab_bar.unsetCursor()
