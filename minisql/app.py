"""Punto de entrada de la aplicación."""

import sys

from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MiniSQL")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
