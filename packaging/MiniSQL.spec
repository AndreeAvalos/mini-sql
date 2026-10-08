# Empaquetado de MiniSQL con PyInstaller (un solo .exe, sin consola).
#
#     pyinstaller packaging/MiniSQL.spec
#     dist/MiniSQL.exe --self-test        # verifica el ejecutable (escribe dist/selftest.log)
#
# Lo usa .github/workflows/release.yml para publicar el .exe en cada versión.
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH lo define PyInstaller)

# Versión de Windows (Propiedades > Detalles), tomada de pyproject.toml: un solo lugar para la versión
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
NUMBERS = (*(int(n) for n in VERSION.split(".")[:3]), 0)
VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(filevers=NUMBERS, prodvers=NUMBERS),
    kids=[
        StringFileInfo([StringTable("0C0A04B0", [
            StringStruct("ProductName", "MiniSQL"),
            StringStruct("FileDescription", "MiniSQL - cliente de escritorio para Oracle"),
            StringStruct("ProductVersion", VERSION),
            StringStruct("FileVersion", VERSION),
            StringStruct("CompanyName", "Andree Avalos"),
            StringStruct("LegalCopyright", "MIT - (c) 2026 Andree Avalos"),
            StringStruct("OriginalFilename", "MiniSQL.exe"),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0C0A, 1200])]),
    ],
)

# Módulos de Qt grandes que la app no usa: dejarlos fuera reduce mucho el tamaño.
QT_UNUSED = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtGraphs", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtBluetooth",
    "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtHttpServer",
    "tkinter", "unittest", "pytest",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    # Imports que PyInstaller no ve: el modo thin de oracledb carga `cryptography` (x509, cifrados) desde
    # código compilado con Cython, y keyring elige su backend en tiempo de ejecución. Sin esto el .exe abre
    # pero no se conecta (DPY-3016). `MiniSQL.exe --self-test` lo comprueba.
    hiddenimports=(collect_submodules("cryptography") + collect_submodules("oracledb")
                   + collect_submodules("keyring.backends") + collect_submodules("minisql")),
    excludes=QT_UNUSED,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MiniSQL",
    console=False,          # aplicación de ventana
    upx=False,              # UPX provoca falsos positivos de antivirus
    version=VERSION_INFO,
)
