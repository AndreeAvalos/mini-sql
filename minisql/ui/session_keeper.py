"""Guardián de sesión: autoguardado y recuperación del espacio de trabajo."""
import json

from PySide6.QtCore import QObject, QTimer

from ..config import AUTOSAVE_MS, LEGACY_SESSION_FILE
from ..workspace import WorkspaceStore, worth_keeping


class SessionKeeper(QObject):
    """Cada pocos segundos guarda conexiones, hojas (texto, nombre, orden) y código sin compilar
    (ver workspace.py; nunca contraseñas). Al abrir la app se recupera todo, y las hojas de una
    conexión que se cierra quedan guardadas ("parked") hasta que vuelvas a conectarte a ese perfil."""

    def __init__(self, window, path=None, interval=AUTOSAVE_MS):
        super().__init__(window)
        self.window = window
        self.store = WorkspaceStore(path, legacy_file=None if path else LEGACY_SESSION_FILE)
        self.saved = self.store.load()                 # lo que quedó de la vez anterior
        self.parked = {p["name"]: p for p in self.saved.get("parked", []) if p.get("name")}
        self.paused = False           # mientras se recupera la sesión no se escribe (se perdería lo pendiente)
        self.timer = QTimer(self)
        self.timer.setInterval(interval)
        self.timer.timeout.connect(self.save)

    def start(self):
        self.timer.start()

    def stop(self):
        self.timer.stop()

    def snapshot(self):
        conns = [t.snapshot() for t in self.window.conn_tabs()]
        return {"connections": conns, "parked": list(self.parked.values())}

    def save(self, data=None):
        if self.paused:
            return
        try:
            self.store.save(data or self.snapshot())
        except OSError as e:
            self.window.statusBar().showMessage(f"No se pudo guardar la sesión: {e}", 10000)

    def park(self, state):
        """Guarda las hojas de una conexión que se cerró (o que no se pudo recuperar)."""
        state = worth_keeping(state)
        if not state:
            return
        old = self.parked.get(state["name"])
        if old:
            seen = {json.dumps(t, sort_keys=True) for t in old["tabs"]}
            state["tabs"] = old["tabs"] + [t for t in state["tabs"] if json.dumps(t, sort_keys=True) not in seen]
        self.parked[state["name"]] = state
        self.save()

    def claim(self, name):
        """Hojas guardadas de ese perfil (y las quita de la reserva)."""
        state = self.parked.pop(name, None)
        if state:
            self.save()
        return state
