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
        conns = [t.snapshot() for t in self.window.saved_tabs()]     # también las que todavía se conectan
        current = self.window.current_conn()           # la conexión que se estaba usando: se vuelve a elegir al abrir
        return {"connections": conns, "parked": list(self.parked.values()),
                "current": current.name if current else None}

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
            def key(tab):                        # misma hoja aunque cambie cuál está activa
                return json.dumps({k: v for k, v in tab.items() if k != "active"}, sort_keys=True)
            seen = {key(t) for t in old["tabs"]}
            if any(t.get("active") for t in state["tabs"]):   # manda la activa de lo último que se cerró
                old_tabs = [{k: v for k, v in t.items() if k != "active"} for t in old["tabs"]]
                active = next(key(t) for t in state["tabs"] if t.get("active"))
                old_tabs = [dict(t, active=True) if key(t) == active else t for t in old_tabs]
            else:
                old_tabs = old["tabs"]
            state["tabs"] = old_tabs + [t for t in state["tabs"] if key(t) not in seen]
        self.parked[state["name"]] = state
        self.save()

    def claim(self, name):
        """Hojas guardadas de ese perfil (y las quita de la reserva)."""
        state = self.parked.pop(name, None)
        if state:
            self.save()
        return state
