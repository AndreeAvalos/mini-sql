"""Autoprueba del ejecutable empaquetado:  MiniSQL.exe --self-test

Comprueba lo que suele romperse al empaquetar con PyInstaller (módulos que no se incluyeron, plugins de
Qt, backend del llavero) sin conectarse a ninguna base. Escribe el resultado en selftest.log junto al
ejecutable (en modo ventana no hay consola) y termina con código 0 si todo está bien.
"""
import contextlib
import os
import sys
import tempfile
import traceback
from pathlib import Path


def _checks():
    import oracledb
    yield "oracledb", f"{oracledb.__version__}, modo thin: {oracledb.is_thin_mode()}"

    # El modo thin necesita `cryptography` para conectarse (DPY-3016 si no se pudo cargar).
    import cryptography.hazmat.primitives.ciphers  # noqa: F401  (si falla, se ve el ImportError real)
    yield "cryptography", "se puede cargar"

    # La red del modo thin: un puerto local cerrado debe dar un error de red de Oracle (DPY-6xxx),
    # no otro error por algo que faltó empaquetar.
    try:
        oracledb.connect(user="x", password="x", dsn="127.0.0.1:1/selftest", tcp_connect_timeout=3)
    except oracledb.Error as e:
        code = str(e).split(":")[0]
        if not code.startswith("DPY-6"):
            raise
        yield "red Oracle", f"responde como se espera ({code})"

    import keyring
    backend = keyring.get_keyring()
    name = type(backend).__name__
    if "fail" in type(backend).__module__ or name == "NullKeyring":
        # En Windows (donde se usa el .exe) siempre hay llavero: si falta, el empaquetado está mal.
        # En Linux sin escritorio (p. ej. GitHub Actions) no hay; la app funciona, solo sin guardar contraseñas.
        if sys.platform == "win32":
            raise RuntimeError(f"el llavero no tiene un backend útil ({name})")
        yield "keyring", f"sin llavero del sistema ({name}): no se guardarán contraseñas"
    else:
        yield "keyring", name

    from .sql.text import format_sql
    yield "sqlparse", format_sql("select a,b from t where x=1").splitlines()[0]

    from PySide6.QtCore import QLibraryInfo
    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow

    if sys.platform == "win32":             # la prueba corre sin pantalla: verificar el plugin real aparte
        platforms = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / "platforms"
        if not (platforms / "qwindows.dll").exists():
            raise RuntimeError(f"falta el plugin de ventanas de Qt en {platforms}")
        yield "plugin Windows", "qwindows.dll incluido"
    app = QApplication.instance() or QApplication([])
    window = MainWindow(session_path=tempfile.mkdtemp(), restore=False)
    window.keeper.stop()
    app.processEvents()
    yield "interfaz", f"ventana principal creada ({app.platformName()})"


def run():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    lines, ok = [], True
    try:
        for name, detail in _checks():
            lines.append(f"OK   {name}: {detail}")
    except Exception:
        ok = False
        lines.append("FALLA\n" + traceback.format_exc())
    report = "\n".join(lines)
    print(report)
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    with contextlib.suppress(OSError):
        (base / "selftest.log").write_text(report + "\n", encoding="utf-8")
    return 0 if ok else 1
