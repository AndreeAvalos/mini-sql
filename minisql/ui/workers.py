"""Hilos de trabajo: llevar el resultado de vuelta al hilo de la interfaz.

Regla de la app: nunca se llama a Oracle desde la interfaz y nunca se tocan widgets desde un
hilo de trabajo. Estas clases hacen ese puente con señales de Qt.
"""
import contextlib
import threading

from PySide6.QtCore import QObject, Signal


class Emitter(QObject):
    """Señales de una ejecución en la sesión principal (hojas y pestaña Datos)."""
    done = Signal(dict)
    failed = Signal(str)
    output = Signal(list)          # líneas de DBMS_OUTPUT (se emite antes de done/failed)


class TaskRunner(QObject):
    """Corre fn() en un hilo y entrega el resultado a on_ok (o el mensaje de error a on_err)
    en el hilo de la interfaz."""
    _finished = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._finished.connect(self._dispatch)

    def run(self, fn, on_ok, on_err):
        def work():
            try:
                callback, result = on_ok, fn()
            except Exception as e:
                callback, result = on_err, str(e)
            self._finished.emit(callback, result)
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _dispatch(callback, result):
        # RuntimeError: el widget que esperaba el resultado ya no existe (pestaña cerrada o refrescada)
        with contextlib.suppress(RuntimeError):
            callback(result)
