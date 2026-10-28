"""Pestaña provisoria de una conexión que se está recuperando al abrir: la forma de la ventana (skeleton) con
"Conectando…" encima. Guarda el estado de la sesión anterior para que nada se pierda si se cierra antes."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QWidget

from .skeleton import Skeleton


class ConnectingTab(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.name = state["name"]
        self.skeleton = Skeleton("window")
        self.card = QFrame()
        self.card.setObjectName("busyCard")
        self.card.setStyleSheet("#busyCard { background: palette(base); border: 1px solid palette(mid);"
                                " border-radius: 8px; }")
        self.label = QLabel()
        row = QHBoxLayout(self.card)
        row.setContentsMargins(18, 10, 18, 10)
        row.addWidget(self.label)
        grid = QGridLayout(self)                      # la tarjeta, centrada encima del skeleton
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(self.skeleton, 0, 0)
        grid.addWidget(self.card, 0, 0, Qt.AlignCenter)
        n = sum(1 for t in state.get("tabs", []) if t.get("kind") == "sheet")
        self.sheets = f" · {n} hoja(s)" if n else ""
        self.label.setText(f"Conectando a <b>{self.name}</b>…{self.sheets}")

    def show_waiting(self):
        self.label.setText(f"Esperando la contraseña de <b>{self.name}</b>…{self.sheets}")

    # --- lo mismo que una ConnectionTab para el guardián y al cerrar
    def snapshot(self):
        return self.state

    def close_connection(self):
        return True
