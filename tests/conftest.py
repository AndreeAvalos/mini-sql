"""Configuración común de las pruebas: Qt sin ventana y el paquete en el path."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # el paquete minisql
sys.path.insert(0, str(Path(__file__).resolve().parent))       # support.py

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])
