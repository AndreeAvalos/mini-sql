"""Panel y diálogo de depuración."""
import contextlib

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..sql.plsql import UNIT_OBJECT
from ..sql.text import clean_statement
from .editors import CodeEditor
from .results import ResultView


class DebugPanel(QWidget):
    """Panel inferior de depuración: botones, ubicación, variables, pila y salida."""

    def __init__(self, debugger):
        super().__init__()
        self.debugger = debugger
        self.buttons = {}
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        title = QLabel("<b>🐞 Depuración</b>")
        bar.addWidget(title)
        for key, text, tip in (("continue", "▶ Continuar", "F5"), ("over", "↷ Paso sobre", "F10"),
                               ("into", "↓ Paso dentro", "F11"), ("out", "↑ Salir", "Shift+F11"),
                               ("stop", "■ Detener", "Shift+F5")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, k=key: self.do(k))
            bar.addWidget(b)
            self.buttons[key] = b
        self.location = QLabel("")
        self.location.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bar.addSpacing(12)
        bar.addWidget(self.location, 1)
        close = QToolButton()
        close.setText("✕")
        close.setToolTip("Ocultar panel")
        close.clicked.connect(self.hide)
        bar.addWidget(close)

        self.vars = ResultView()
        self.stack = QPlainTextEdit()
        self.stack.setReadOnly(True)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Mensajes y DBMS_OUTPUT")
        side = QTabWidget()
        side.setDocumentMode(True)
        side.addTab(self.stack, "Pila de llamadas")
        side.addTab(self.output, "Salida")
        self.side = side
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.vars)
        split.addWidget(side)
        split.setSizes([500, 400])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(split, 1)
        self.set_running(False, active=False)

    def do(self, key):
        if key == "stop":
            self.debugger.stop()
        else:
            self.debugger.command(key)
        self.set_running(self.debugger.running, self.debugger.active)

    def set_running(self, running, active=True):
        for key, b in self.buttons.items():
            b.setEnabled(active and (key == "stop" or not running))
        if active and running:
            self.location.setText("Ejecutando…")

    def show_stop(self, info, where):
        self.set_running(False)
        self.location.setText(where)
        self.vars.show_rows(["Variable", "Valor"], info.get("variables", []))
        self.stack.setPlainText(info.get("backtrace", ""))

    def log(self, text):
        self.output.appendPlainText(text)


class DebugStartDialog(QDialog):
    """Pide el bloque anónimo que llama al código a depurar."""

    def __init__(self, parent, title, block, can_compile):
        super().__init__(parent)
        self.setWindowTitle(f"Depurar {title}")
        self.resize(700, 420)
        info = QLabel("Ajusta los valores de los argumentos. La depuración se detiene en los puntos de "
                      "interrupción (clic en el margen o F9) o, si no hay, en la primera línea.")
        info.setWordWrap(True)
        self.editor = CodeEditor()
        self.editor.setPlainText(block)
        self.compile_debug = QCheckBox("Compilar con información de depuración (ALTER … COMPILE DEBUG)")
        self.compile_debug.setChecked(can_compile)
        self.compile_debug.setEnabled(can_compile)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Depurar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.compile_debug)
        layout.addWidget(buttons)

    def block(self):
        return clean_statement(self.editor.toPlainText())


class DebugController(QObject):
    """Une el depurador con la interfaz: abre el objeto donde se detuvo, marca la línea y
    muestra el resultado en el panel.

    open_object(dueño, nombre, tipo) -> visor;  on_transaction(bool): avisa si quedaron cambios pendientes.
    Es QObject para que las señales del hilo del depurador lleguen en el hilo de la interfaz."""

    def __init__(self, debugger, panel, open_object, on_transaction, parent=None):
        super().__init__(parent)
        self.debugger = debugger
        self.panel = panel
        self.open_object = open_object
        self.on_transaction = on_transaction
        self.viewer = None                   # visor con la línea marcada
        debugger.stopped.connect(self.stopped)
        debugger.message.connect(panel.log)
        debugger.resumed.connect(self.resumed)
        debugger.ended.connect(self.ended)

    def start(self, block, compile_targets=(), target=None):
        if self.debugger.active:
            return
        panel = self.panel
        panel.output.clear()
        panel.vars.show_rows(["Variable", "Valor"], [])
        panel.stack.clear()
        panel.show()
        panel.log("Iniciando depuración…")
        self.debugger.start(block, list(self.debugger.bps), compile_targets, target)
        panel.set_running(True)

    def _clear_line(self):
        if self.viewer is not None:
            with contextlib.suppress(RuntimeError):   # el visor ya se cerró
                self.viewer.clear_exec_line()
            self.viewer = None

    def resumed(self):
        """Mientras corre no hay línea marcada, y variables y pila quedan en gris (ya no son actuales)."""
        self._clear_line()
        self.panel.vars.setEnabled(False)
        self.panel.stack.setEnabled(False)

    def stopped(self, info):
        self._clear_line()
        self.panel.vars.setEnabled(True)
        self.panel.stack.setEnabled(True)
        if info.get("utype"):
            where = f"{info['owner']}.{info['name']} ({info['utype'].lower()}) · línea {info['line']}"
            self.viewer = self.open_object(info["owner"], info["name"],
                                           UNIT_OBJECT.get(info["utype"], info["utype"]))
            self.viewer.show_exec_line(info["utype"], info["line"], info.get("variables", []))
        else:
            where = f"Bloque de prueba · línea {info['line']}"
        self.panel.show_stop(info, where)

    def ended(self, res):
        self._clear_line()
        panel = self.panel
        panel.set_running(False, active=False)
        if res.get("error"):
            panel.location.setText("Terminó con error")
            panel.log(res["error"])
        else:
            panel.location.setText("Terminado")
            panel.log(f"{res.get('message', 'Terminado')}  ·  {res.get('elapsed', 0):.2f} s")
        if res.get("output"):
            panel.log("--- DBMS_OUTPUT ---")
            for line in res["output"]:
                panel.log(line)
            panel.side.setCurrentWidget(panel.output)
        if res.get("txn") is not None:
            self.on_transaction(bool(res["txn"]))
