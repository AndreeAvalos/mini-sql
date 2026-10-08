"""Diálogo de conexión."""

from pathlib import Path

import oracledb
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
)

try:
    import keyring  # guarda contraseñas en el llavero del sistema (Windows Credential Manager, etc.)
except ImportError:
    keyring = None

import contextlib

from ..config import KEYRING_SERVICE, load_profiles, load_settings, save_profiles, save_settings
from ..db.session import OracleSession
from ..db.tns import default_tns_path, parse_tnsnames
from .style import ERROR_COLOR, MUTED_COLOR


class ConnectDialog(QDialog):
    def __init__(self, parent=None, preset=None):
        """preset: conexión de una sesión guardada ({name, user, dsn, tabs}) para recuperarla.
        Si su contraseña está en el llavero, conecta sola."""
        super().__init__(parent)
        self.setWindowTitle("Nueva conexión" if not preset else f"Recuperar sesión: {preset['name']}")
        self.setMinimumWidth(420)
        self.profiles = load_profiles()
        self.session = None
        self.conn_name = ""

        self.combo = QComboBox()
        self.combo.addItem("(nueva)")
        for p in self.profiles:
            self.combo.addItem(p["name"])
        self.name = QLineEdit()
        self.name.setPlaceholderText("Ej. PROD, DESARROLLO…")
        self.user = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.tns = {}
        self.tns_path = QLineEdit(default_tns_path())
        self.tns_path.setPlaceholderText("Ruta de tnsnames.ora (opcional)")
        browse = QPushButton("Examinar…")
        browse.clicked.connect(self.browse_tns)
        self.tns_path.editingFinished.connect(self.load_tns)
        tns_row = QHBoxLayout()
        tns_row.addWidget(self.tns_path, 1)
        tns_row.addWidget(browse)
        self.tns_info = QLabel("")
        self.tns_info.setStyleSheet(f"color: {MUTED_COLOR};")

        self.dsn = QComboBox()
        self.dsn.setEditable(True)
        self.dsn.setInsertPolicy(QComboBox.NoInsert)
        self.dsn.lineEdit().setPlaceholderText("Alias del tnsnames o host:1521/SERVICIO")
        self.dsn.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.dsn.completer().setFilterMode(Qt.MatchContains)
        self.dsn.currentTextChanged.connect(self.update_dsn_tooltip)
        self.save = QCheckBox("Guardar perfil (sin contraseña)")
        self.save.setChecked(True)
        self.save_pwd = QCheckBox("Guardar contraseña en el llavero del sistema")
        self.save_pwd.setEnabled(keyring is not None)
        if keyring is None:
            self.save_pwd.setToolTip("Instala 'keyring' para habilitar esta opción")

        form = QFormLayout(self)
        if preset:
            n = sum(1 for t in preset.get("tabs", []) if t.get("kind") == "sheet")
            note = QLabel(f"Se recuperarán {n} hoja(s) de la sesión anterior. "
                          "Si cancelas, se guardan para cuando te conectes a este perfil.")
            note.setWordWrap(True)
            form.addRow(note)
        form.addRow("Perfil", self.combo)
        form.addRow("Nombre", self.name)
        form.addRow("Usuario", self.user)
        form.addRow("Contraseña", self.password)
        form.addRow("tnsnames.ora", tns_row)
        form.addRow("", self.tns_info)
        form.addRow("DSN", self.dsn)
        form.addRow(self.save)
        form.addRow(self.save_pwd)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Conectar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.try_connect)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.combo.currentIndexChanged.connect(self.load_profile)
        self.load_tns()
        if preset:
            self.apply_preset(preset)
        elif self.profiles:
            self.combo.setCurrentIndex(1)

    def apply_preset(self, preset):
        names = [p["name"] for p in self.profiles]
        if preset["name"] in names:
            self.combo.setCurrentIndex(names.index(preset["name"]) + 1)
        else:
            self.name.setText(preset["name"])
            self.user.setText(preset.get("user") or "")
            self.dsn.setCurrentText(preset.get("dsn") or "")
            self.password.setFocus()
        if self.password.text():
            QTimer.singleShot(0, self.try_connect)   # contraseña en el llavero: reconectar sin preguntar

    def browse_tns(self):
        start = self.tns_path.text().strip() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Selecciona tnsnames.ora", start, "Archivos Oracle (*.ora);;Todos los archivos (*)")
        if path:
            self.tns_path.setText(path)
            self.load_tns()

    def load_tns(self):
        path = self.tns_path.text().strip()
        if path and Path(path).is_dir():          # si dan una carpeta, buscar tnsnames.ora dentro
            path = str(Path(path) / "tnsnames.ora")
            self.tns_path.setText(path)
        current = self.dsn.currentText()
        self.tns = {}
        if path:
            try:
                self.tns = parse_tnsnames(path)
                self.tns_info.setStyleSheet(f"color: {MUTED_COLOR};")
                self.tns_info.setText(f"{len(self.tns)} alias encontrados")
                settings = load_settings()
                settings["tns_path"] = path
                save_settings(settings)
            except OSError as e:
                self.tns_info.setStyleSheet(f"color: {ERROR_COLOR};")
                self.tns_info.setText(f"No se pudo leer: {e.strerror or e}")
        else:
            self.tns_info.setText("")
        self.dsn.blockSignals(True)
        self.dsn.clear()
        self.dsn.addItems(sorted(self.tns))
        self.dsn.setCurrentText(current)
        self.dsn.blockSignals(False)
        self.update_dsn_tooltip(current)

    def resolve_dsn(self, text):
        """Si es un alias del tnsnames devuelve su descriptor; si no, el texto tal cual."""
        return self.tns.get(text.strip().upper(), text.strip())

    def update_dsn_tooltip(self, text):
        desc = self.tns.get(text.strip().upper())
        self.dsn.setToolTip(desc or "")

    def load_profile(self, idx):
        if idx <= 0:
            return
        p = self.profiles[idx - 1]
        self.name.setText(p["name"])
        self.user.setText(p["user"])
        self.dsn.setCurrentText(p["dsn"])
        pwd = ""
        if keyring:
            with contextlib.suppress(Exception):
                pwd = keyring.get_password(KEYRING_SERVICE, p["name"]) or ""
        self.password.setText(pwd)
        self.save_pwd.setChecked(bool(pwd))
        (self.password if not pwd else self.user).setFocus()

    def try_connect(self):
        user, dsn = self.user.text().strip(), self.dsn.currentText().strip()
        name = self.name.text().strip() or user
        if not user or not dsn:
            QMessageBox.warning(self, "Faltan datos", "Escribe usuario y DSN.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            session = OracleSession.open(user, self.password.text(), self.resolve_dsn(dsn))
        except oracledb.Error as e:
            QMessageBox.critical(self, "No se pudo conectar", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()

        if self.save.isChecked():
            self.profiles = [p for p in self.profiles if p["name"] != name]
            self.profiles.append({"name": name, "user": user, "dsn": dsn})
            save_profiles(self.profiles)
            if keyring:
                try:
                    if self.save_pwd.isChecked():
                        keyring.set_password(KEYRING_SERVICE, name, self.password.text())
                    else:
                        keyring.delete_password(KEYRING_SERVICE, name)
                except Exception:
                    pass

        self.session, self.conn_name, self.dsn_label = session, name, dsn
        self.accept()
