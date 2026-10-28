"""Capa de "ocupado" encima de los resultados: mientras se ejecuta, guarda o trae filas, tapa lo anterior
(que no se pueda editar ni guardar algo que está por cambiar) y muestra spinner, segundos y Cancelar."""
import time

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

SPINNER = "◐◓◑◒"


class BusyOverlay(QWidget):
    """Se pone encima de todo su padre y lo sigue al cambiar de tamaño. start(texto, on_cancel) / stop()."""

    def __init__(self, parent):
        super().__init__(parent)
        self.card = QFrame(self)
        self.card.setObjectName("busyCard")
        self.card.setStyleSheet("#busyCard { background: palette(base); border: 1px solid palette(mid);"
                                " border-radius: 8px; }")
        row = QHBoxLayout(self.card)
        row.setContentsMargins(16, 10, 12, 10)
        row.setSpacing(12)
        self.label = QLabel()
        self.cancel_btn = QPushButton("■ Cancelar")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.label)
        row.addWidget(self.cancel_btn)
        column = QVBoxLayout(self)
        column.addStretch(1)
        column.addWidget(self.card, 0, Qt.AlignCenter)
        column.addStretch(2)

        self.timer = QTimer(self)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self._tick)
        self.text, self.started, self.frame, self._on_cancel = "", 0.0, 0, None
        self.cancel_btn.clicked.connect(self._cancel)
        parent.installEventFilter(self)
        self.hide()

    @property
    def active(self):
        return self.timer.isActive()

    def start(self, text, on_cancel=None):
        self.text, self.started, self.frame, self._on_cancel = text, time.perf_counter(), 0, on_cancel
        self.cancel_btn.setVisible(on_cancel is not None)
        self.cancel_btn.setEnabled(True)
        self.setGeometry(self.parentWidget().rect())
        self._tick()
        self.show()
        self.raise_()
        self.timer.start()

    def set_text(self, text):
        self.text = text
        self._tick()

    def stop(self):
        self.timer.stop()
        self.hide()

    def _cancel(self):
        if self._on_cancel:
            self.cancel_btn.setEnabled(False)            # un clic basta: Oracle responde con ORA-01013
            self.text = "Cancelando…"
            self._tick()
            self._on_cancel()

    def _tick(self):
        spin = SPINNER[self.frame % len(SPINNER)]
        self.frame += 1
        self.label.setText(f"{spin}  {self.text}  {time.perf_counter() - self.started:.1f} s")

    # --- seguir al padre y tapar lo de abajo
    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(obj.rect())
        return False

    dim = True                                           # False: debajo hay un skeleton, que se vea sin apagar

    def paintEvent(self, _event):
        if not self.dim:
            return
        color = self.palette().window().color()
        color.setAlpha(170)                              # lo de abajo se ve, apagado
        QPainter(self).fillRect(self.rect(), color)

    def mousePressEvent(self, event):
        event.accept()                                   # los clics no llegan a la tabla de abajo

    mouseDoubleClickEvent = mousePressEvent

    def wheelEvent(self, event):
        event.accept()
