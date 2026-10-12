"""Actualizaciones en la interfaz: aviso de versión nueva, descarga con progreso, instalación al reiniciar
y volver a la versión anterior. La lógica (GitHub, huella SHA-256, reemplazo del .exe) está en minisql/update.py.

Nunca se instala con trabajo pendiente: el reinicio pasa por el cierre normal de la ventana, que pregunta por
transacciones sin confirmar, consultas en curso y cambios sin guardar, y guarda la sesión antes de instalar.
"""
import time

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QProgressDialog, QToolButton

from .. import __version__, update
from ..config import UPDATES_DIR, load_settings, save_settings
from .style import WARN_COLOR
from .workers import TaskRunner


class UpdateBanner(QFrame):
    """Barra discreta arriba de las pestañas: "MiniSQL X disponible · Novedades · Actualizar · Omitir"."""

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.setObjectName("updateBanner")
        self.setStyleSheet(f"#updateBanner {{ border-bottom: 2px solid {WARN_COLOR}; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        self.label = QLabel()
        row.addWidget(self.label, 1)
        for text, slot in (("Novedades", manager.show_notes), ("⬇ Actualizar", manager.update_now),
                           ("Omitir esta versión", manager.skip), ("✕", manager.dismiss)):
            b = QToolButton()
            b.setText(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        self.hide()

    def show_release(self, release):
        kind = " (versión de prueba)" if release.prerelease else ""
        self.label.setText(f"⬆ <b>MiniSQL {release.version}</b>{kind} está disponible (tienes la {__version__}).")
        self.show()


class UpdateManager(QObject):
    progress = Signal(int, int)

    def __init__(self, window, load=load_settings, save=save_settings, releases=None, frozen=None, exe=None,
                 downloads=UPDATES_DIR):
        """load/save: cómo leer y guardar config.json; releases(): la lista de GitHub (las pruebas la
        reemplazan); frozen/exe: si corre como .exe y cuál; downloads: carpeta de descargas."""
        super().__init__(window)
        self.window = window
        self._load, self._save = load, save
        self._releases = releases or update.fetch_releases
        self.frozen = update.is_frozen() if frozen is None else frozen
        self.exe = exe or (update.current_exe() if self.frozen else None)
        self.downloads = downloads
        self.runner = TaskRunner(self)
        self.banner = UpdateBanner(self)
        self.release = None             # la versión nueva encontrada
        self.pending = None             # ("install", ruta) o ("rollback", None): se aplica al cerrar
        self.restart = False            # reabrir la app después de aplicar lo pendiente
        self.checking = False
        self.downloading = False
        self._cancel = False

    # --- ajustes (config.json → "updates")
    @property
    def settings(self):
        return dict(self._load().get("updates") or {})

    def _set(self, **values):
        data = self._load()
        data["updates"] = {**(data.get("updates") or {}), **values}
        self._save(data)

    # --- al abrir
    def start(self):
        """Solo en el .exe: avisa si se acaba de actualizar, limpia descargas viejas y programa la revisión."""
        if not self.frozen:
            return
        last = self.settings.get("last_run_version")
        if last != __version__:
            if last and update.is_newer(__version__, last):
                self.window.statusBar().showMessage(
                    f"MiniSQL se actualizó a la versión {__version__} (antes {last}).", 20000)
            self._set(last_run_version=__version__)
        update.clean_downloads(self.downloads)
        if update.should_check(self.settings):
            QTimer.singleShot(4000, lambda: self.check(manual=False))

    # --- revisar
    def check(self, manual=True):
        if self.checking or self.downloading:
            return
        self.checking = True
        prerelease = bool(self.settings.get("prerelease"))
        self.runner.run(lambda: update.latest_release(__version__, prerelease, self._releases()),
                        lambda r: self._checked(r, manual), lambda msg: self._check_failed(msg, manual))

    def _checked(self, release, manual):
        self.checking = False
        self._set(last_check=time.time())
        if release is None:
            if manual:
                QMessageBox.information(self.window, "Actualizaciones",
                                        f"Tienes la versión más reciente ({__version__}).")
            return
        if not manual and release.version == self.settings.get("skip"):
            return                       # el usuario pidió omitir esta versión
        self.release = release
        self.banner.show_release(release)

    def _check_failed(self, msg, manual):
        self.checking = False
        if manual:                       # la revisión automática falla en silencio (sin red, GitHub bloqueado…)
            QMessageBox.warning(self.window, "Actualizaciones", msg)

    # --- acciones del aviso
    def show_notes(self):
        if not self.release:
            return
        box = QMessageBox(self.window)
        box.setWindowTitle(f"Novedades de MiniSQL {self.release.version}")
        box.setTextFormat(Qt.MarkdownText)
        box.setText(self.release.notes.strip() or "Sin notas de versión.")
        page = box.addButton("Abrir en GitHub", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is page:
            QDesktopServices.openUrl(QUrl(self.release.page))

    def skip(self):
        if self.release:
            self._set(skip=self.release.version)
        self.dismiss()

    def dismiss(self):
        self.banner.hide()

    def update_now(self):
        release = self.release
        if release is None or self.downloading:
            return
        if not self.frozen:
            QMessageBox.information(self.window, "Actualizar",
                                    "Estás usando MiniSQL desde el código (python main.py): actualízalo con "
                                    "git pull. Se abrirá la página de la versión.")
            QDesktopServices.openUrl(QUrl(release.page))
            return
        if not update.can_replace(self.exe):
            QMessageBox.warning(self.window, "Actualizar",
                                f"No hay permiso para reemplazar {self.exe}.\n\nMueve MiniSQL.exe a una carpeta "
                                "tuya (por ejemplo %LOCALAPPDATA%\\Programs\\MiniSQL) para que se actualice solo. "
                                "Por ahora se abrirá la página para descargarlo a mano.")
            QDesktopServices.openUrl(QUrl(release.page))
            return
        self._download(release)

    def _download(self, release):
        self.downloading, self._cancel = True, False
        dialog = QProgressDialog(f"Descargando MiniSQL {release.version}…", "Cancelar", 0, 100, self.window)
        dialog.setWindowTitle("Actualizar MiniSQL")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.canceled.connect(lambda: setattr(self, "_cancel", True))
        self.progress.connect(lambda done, total: dialog.setValue(int(done * 100 / total) if total else 0))
        self.dialog = dialog

        def work():
            path = update.download(release, self.downloads, self.progress.emit, lambda: self._cancel)
            ok, report = update.self_test(path)
            if not ok:
                path.unlink(missing_ok=True)
                raise update.UpdateError("La versión descargada no pasó su autoprueba; no se instala.\n\n"
                                         + report[-1500:])
            return path

        self.runner.run(work, lambda path: self._downloaded(release, path), self._download_failed)

    def _finish_download(self):
        self.downloading = False
        self.progress.disconnect()
        self.dialog.reset()
        self.dialog.deleteLater()

    def _download_failed(self, msg):
        self._finish_download()
        if not msg.startswith("Descarga cancelada"):
            QMessageBox.warning(self.window, "Actualizar MiniSQL", msg)

    def _downloaded(self, release, path):
        self._finish_download()
        self.pending = ("install", path)
        self.banner.label.setText(f"✔ MiniSQL {release.version} descargado y verificado: se instala al cerrar.")
        r = QMessageBox.question(
            self.window, "Actualizar MiniSQL",
            f"MiniSQL {release.version} está descargado y verificado.\n\n"
            "Para instalarlo hay que reiniciar la app. Tus hojas se guardan y se recuperan al abrir; si hay "
            "cambios sin confirmar o sin guardar, te preguntará antes.\n\n¿Reiniciar ahora? "
            "(Si eliges No, se instala la próxima vez que cierres MiniSQL.)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r == QMessageBox.Yes:
            self.restart_now()

    def restart_now(self):
        """Cierra la ventana por el camino normal (con todas sus confirmaciones); si se cierra, se aplica lo
        pendiente y la app se vuelve a abrir."""
        self.restart = True
        if not self.window.close():
            self.restart = False         # el usuario canceló el cierre: queda pendiente para después

    # --- volver a la versión anterior
    def can_rollback(self):
        return self.frozen and update.previous_exe(self.exe).exists()

    def rollback_now(self):
        if not self.can_rollback():
            return
        previous = self.settings.get("previous_version") or "anterior"
        r = QMessageBox.question(self.window, "Volver a la versión anterior",
                                 f"Se reinstalará la versión {previous} y MiniSQL se reiniciará. "
                                 f"La {__version__} no se volverá a ofrecer automáticamente.\n\n¿Continuar?")
        if r == QMessageBox.Yes:
            self.pending = ("rollback", None)
            self.restart_now()

    # --- al cerrar la ventana (después de guardar la sesión)
    def on_exit(self):
        """Aplica lo pendiente. Devuelve el .exe a reabrir, o None."""
        if not self.pending:
            return None
        action, path = self.pending
        self.pending = None
        try:
            if action == "install":
                update.install(path, self.exe)
                self._set(previous_version=__version__, skip=None)
            else:
                update.rollback(self.exe)
                self._set(previous_version=__version__, skip=__version__)
        except Exception as e:
            QMessageBox.critical(self.window, "Actualizar MiniSQL",
                                 f"No se pudo {'instalar la actualización' if action == 'install' else 'volver atrás'}:"
                                 f"\n{e}\n\nMiniSQL sigue en la versión {__version__}.")
            return None
        return self.exe if self.restart else None

    # --- menú
    def build_menu(self, menu: QMenu):
        menu.clear()
        menu.addAction("Buscar actualizaciones…", lambda: self.check(manual=True))
        auto = menu.addAction("Buscar actualizaciones al abrir")
        auto.setCheckable(True)
        auto.setChecked(bool(self.settings.get("auto", True)))
        auto.toggled.connect(lambda on: self._set(auto=on))
        pre = menu.addAction("Incluir versiones de prueba")
        pre.setCheckable(True)
        pre.setChecked(bool(self.settings.get("prerelease")))
        pre.toggled.connect(lambda on: self._set(prerelease=on))
        if self.can_rollback():
            previous = self.settings.get("previous_version")
            menu.addAction(f"Volver a la versión anterior{f' ({previous})' if previous else ''}…", self.rollback_now)
        menu.addSeparator()
        menu.addAction("Acerca de MiniSQL", self.about)

    def about(self):
        QMessageBox.about(self.window, "Acerca de MiniSQL",
                          f"<b>MiniSQL {__version__}</b><br>Cliente de escritorio para Oracle.<br><br>"
                          f'<a href="{update.RELEASES_PAGE}">Versiones y novedades</a>')
