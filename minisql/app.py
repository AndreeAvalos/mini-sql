"""Punto de entrada de la aplicación."""

import sys


def main():
    if "--self-test" in sys.argv:          # verificación del ejecutable empaquetado (ver selftest.py)
        from .selftest import run
        sys.exit(run())

    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("MiniSQL")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
