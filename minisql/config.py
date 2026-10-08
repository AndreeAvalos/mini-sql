"""Configuración: rutas de archivos, límites y perfiles/ajustes guardados."""

import json
from pathlib import Path

APP_DIR = Path.home() / ".minisql"


PROFILES_FILE = APP_DIR / "conexiones.json"


SETTINGS_FILE = APP_DIR / "config.json"


SESSION_DIR = APP_DIR / "sesion"                # conexiones y hojas abiertas (sin contraseñas)
LEGACY_SESSION_FILE = APP_DIR / "sesion.json"   # formato anterior: se migra al abrir


AUTOSAVE_MS = 3000


KEYRING_SERVICE = "minisql-oracle"


MAX_ROWS = 1000          # filas máximas que se traen por consulta


MAX_CELL_CHARS = 300     # texto visible por celda (al copiar se copia completo)


MAX_OUTPUT_LINES = 20000 # líneas de DBMS_OUTPUT que se leen por ejecución


def load_profiles():
    try:
        return json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_profiles(profiles):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_FILE.write_text(json.dumps(profiles, indent=2, ensure_ascii=False), encoding="utf-8")


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_settings(settings):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
