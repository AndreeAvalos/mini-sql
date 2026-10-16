"""Configuración común de las pruebas: Qt sin ventana y el paquete en el path."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # el paquete minisql
sys.path.insert(0, str(Path(__file__).resolve().parent))       # support.py

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(autouse=True)
def isolated_font_zoom(monkeypatch):
    """El zoom de letra (Ctrl+rueda) se guarda en config.json: en las pruebas, en memoria y en el tamaño normal."""
    from minisql.ui.style import font_zoom
    store = {}
    monkeypatch.setattr(font_zoom, "_load", lambda: dict(store))
    monkeypatch.setattr(font_zoom, "_save", store.update)
    monkeypatch.setattr(font_zoom, "_size", None)
    from minisql.ui.results_dock import placement  # lado de los resultados: igual, en memoria
    places = {}
    monkeypatch.setattr(placement, "_load", lambda: dict(places))
    monkeypatch.setattr(placement, "_save", places.update)
    monkeypatch.setattr(placement, "_position", None)


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])
