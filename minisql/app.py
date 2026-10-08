"""Punto de entrada de la aplicación."""

import sys

from .config import APP_DIR

ALREADY_OPEN = ("MiniSQL ya está abierto en otra ventana.\n\n"
                "Usa esa ventana: ahí puedes abrir varias conexiones (+ Conexión) y hojas en pestañas. "
                "Dos MiniSQL abiertos a la vez se pisarían el autoguardado de las hojas.")


def single_instance_lock(path=None):
    """Candado para que haya un solo MiniSQL abierto: el guardián de sesión de cada instancia escribe en
    la misma carpeta y dos a la vez se pisarían. Devuelve el candado (hay que conservarlo mientras la app
    esté abierta) o None si ya hay otra instancia. Si la otra se cerró de golpe, el candado se libera solo
    (QLockFile revisa si su proceso sigue vivo)."""
    from PySide6.QtCore import QLockFile

    path = path or APP_DIR / "minisql.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(path))
    lock.setStaleLockTime(0)       # la app puede estar abierta horas: el candado no vence por tiempo
    return lock if lock.tryLock(200) else None


def main():
    if "--self-test" in sys.argv:          # verificación del ejecutable empaquetado (ver selftest.py)
        from .selftest import run
        sys.exit(run())

    from PySide6.QtWidgets import QApplication, QMessageBox

    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("MiniSQL")
    lock = single_instance_lock()
    if lock is None:
        QMessageBox.information(None, "MiniSQL", ALREADY_OPEN)
        sys.exit(0)
    w = MainWindow()
    w.show()
    code = app.exec()
    lock.unlock()
    sys.exit(code)
