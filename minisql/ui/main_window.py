"""Ventana principal."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import load_settings, save_settings
from ..db.session import OracleSession
from .connect_dialog import ConnectDialog, saved_login
from .connecting_tab import ConnectingTab
from .connection_tab import ConnectionTab
from .session_keeper import SessionKeeper
from .style import font_zoom
from .updates import UpdateManager
from .workers import TaskRunner


class MainWindow(QMainWindow):
    def __init__(self, session_path=None, restore=True, updates=None):
        """updates: fábrica del UpdateManager, updates(ventana) (las pruebas usan una sin red ni config.json real)."""
        super().__init__()
        self.setWindowTitle("MiniSQL")
        self.relaunch_exe = None          # .exe a abrir al terminar (después de instalar una actualización)
        self.closing = False
        self.runner = TaskRunner(self)    # conexiones que se recuperan al abrir, sin bloquear la ventana
        self.resize(1200, 800)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_conn)
        btn = QToolButton()
        btn.setAutoRaise(True)
        btn.setText("+ Conexión")
        btn.setToolTip("Nueva conexión (Ctrl+N)")
        btn.clicked.connect(self.new_connection)
        self.updates = (updates or UpdateManager)(self)
        more = QToolButton()
        more.setAutoRaise(True)
        more.setText("⋯")
        more.setToolTip("Actualizaciones y acerca de MiniSQL")
        more.setPopupMode(QToolButton.InstantPopup)
        self.app_menu = QMenu(more)
        self.app_menu.aboutToShow.connect(self._build_app_menu)
        more.setMenu(self.app_menu)
        corner = QWidget()
        corner_row = QHBoxLayout(corner)
        corner_row.setContentsMargins(0, 0, 0, 0)
        corner_row.setSpacing(2)
        corner_row.addWidget(btn)
        corner_row.addWidget(more)
        self.tabs.setCornerWidget(corner, Qt.TopRightCorner)
        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.updates.banner)          # aviso de versión nueva (oculto si no hay)
        column.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self.new_connection)
        QShortcut(QKeySequence("Ctrl+T"), self).activated.connect(self.new_sheet)
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(self.close_current_sheet)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(font_zoom.reset)   # letra normal (Ctrl+rueda)

        self.keeper = SessionKeeper(self, session_path)
        if restore:
            QTimer.singleShot(0, self, self.restore_session)   # el guardián arranca al terminar de recuperar
        else:
            self.keeper.start()
        self.updates.start()

    THEME_CHOICES = (("system", "Como Windows"), ("dark", "Oscuro"), ("light", "Claro"))

    def _build_app_menu(self):
        """Menú ⋯: tema y, abajo, actualizaciones y acerca de (los arma UpdateManager)."""
        self.updates.build_menu(self.app_menu)
        themes = QMenu("Tema", self.app_menu)
        current = load_settings().get("theme", "system")
        for key, label in self.THEME_CHOICES:
            action = themes.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == current)
            action.triggered.connect(lambda _checked=False, k=key: self.set_theme(k))
        first = self.app_menu.actions()[0]
        self.app_menu.insertMenu(first, themes)
        self.app_menu.insertSeparator(first)

    def set_theme(self, key):
        data = load_settings()
        if data.get("theme", "system") == key:
            return
        data["theme"] = key
        save_settings(data)
        QMessageBox.information(self, "Tema", "El tema nuevo se aplica al volver a abrir MiniSQL.")

    def conn_tabs(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count())
                if isinstance(self.tabs.widget(i), ConnectionTab)]

    def saved_tabs(self):
        """Lo que guarda el guardián: las conexiones abiertas y las que se están recuperando, en orden."""
        return [self.tabs.widget(i) for i in range(self.tabs.count())
                if isinstance(self.tabs.widget(i), (ConnectionTab, ConnectingTab))]

    # --- recuperar la sesión anterior
    def restore_session(self):
        """Al abrir: vuelve a conectar y abrir las hojas de la sesión anterior. Las conexiones con la contraseña
        en el llavero se abren a la vez y sin ventanas (cada pestaña muestra un skeleton mientras tanto); las
        demás (o las que fallan) piden la contraseña después, una por una."""
        states = self.keeper.saved.get("connections") or []
        if not states:
            self.keeper.start()
            self.new_connection()
            return
        self.keeper.paused = True
        self._connecting, self._to_ask = 0, []
        for state in states:
            holder = ConnectingTab(state)
            self.tabs.addTab(holder, f"◌ {state['name']}")
            login = saved_login(state)
            if login is None:
                self._to_ask.append((holder, None))
                continue
            self._connecting += 1
            self.runner.run(lambda lg=login: OracleSession.open(lg.user, lg.password, lg.dsn),
                            lambda session, h=holder, lg=login: self._auto_connected(h, session, lg),
                            lambda msg, h=holder: self._auto_failed(h, msg))
        self.select_connection(self.keeper.saved.get("current"))
        if not self._connecting:
            self._ask_remaining()

    def _auto_connected(self, holder, session, login):
        if self.tabs.indexOf(holder) < 0:          # se cerró la pestaña mientras conectaba (ya quedó guardada)
            session.close()
        else:
            self._replace_holder(holder, session, login.name, login.dsn_label)
        self._connected_one()

    def _auto_failed(self, holder, msg):
        if self.tabs.indexOf(holder) >= 0:
            holder.show_waiting()
            self._to_ask.append((holder, msg))     # se pide la contraseña con el error a la vista
        self._connected_one()

    def _connected_one(self):
        self._connecting -= 1
        if not self._connecting:
            self._ask_remaining()

    def _ask_remaining(self):
        if self.closing:
            return
        current = self.keeper.saved.get("current")
        for holder, error in self._to_ask:
            if self.tabs.indexOf(holder) < 0:
                continue
            self.tabs.setCurrentWidget(holder)
            holder.show_waiting()
            dlg = ConnectDialog(self, preset=holder.state, error=error)
            if dlg.exec() == QDialog.Accepted:
                self._replace_holder(holder, dlg.session, dlg.conn_name, dlg.dsn_label)
            else:
                self.tabs.removeTab(self.tabs.indexOf(holder))
                holder.deleteLater()
                self.keeper.park(holder.state)     # no se pierde: vuelve al conectarse a ese perfil
        self._to_ask = []
        self.select_connection(current)            # la última usada, no la de más a la derecha
        self.keeper.paused = False
        self.keeper.save()
        self.keeper.start()
        if self.keeper.parked:
            names = ", ".join(sorted(self.keeper.parked))
            self.statusBar().showMessage(
                f"Hojas guardadas de {names}: se recuperan al conectarte a ese perfil.", 15000)

    def _replace_holder(self, holder, session, name, dsn_label):
        """Pone la conexión real en el lugar de la pestaña que estaba conectando."""
        state = holder.state
        focus = self.tabs.currentWidget() is holder          # no saltar de pestaña cada vez que termina una
        tab = self.add_connection(session, name, dsn_label, index=self.tabs.indexOf(holder), focus=focus)
        self.tabs.removeTab(self.tabs.indexOf(holder))
        holder.deleteLater()
        if name == state["name"]:
            tab.restore(state)
        else:
            self.keeper.park(state)
        return tab

    def add_connection(self, session, name, dsn_label=None, index=None, focus=True):
        tab = ConnectionTab(session, name, dsn_label)
        idx = self.tabs.addTab(tab, name) if index is None else self.tabs.insertTab(index, tab, name)
        tab.pending_changed.connect(lambda t=tab: self.update_title(t))
        if focus:
            self.tabs.setCurrentIndex(idx)
        parked = self.keeper.claim(name)
        if parked:
            tab.restore(parked)
            self.statusBar().showMessage(f"Se recuperaron las hojas guardadas de {name}.", 10000)
        return tab

    def select_connection(self, name):
        """Elige la pestaña de la conexión con ese nombre (si está abierta)."""
        for tab in self.saved_tabs():
            if tab.name == name:
                self.tabs.setCurrentWidget(tab)
                return True
        return False

    def current_conn(self):
        w = self.tabs.currentWidget()
        return w if isinstance(w, ConnectionTab) else None

    def new_connection(self):
        dlg = ConnectDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        self.add_connection(dlg.session, dlg.conn_name, dlg.dsn_label)

    def new_sheet(self):
        c = self.current_conn()
        if c:
            c.add_sheet()

    def close_current_sheet(self):
        c = self.current_conn()
        if c:
            c.close_sheet(c.sheets.currentIndex())

    def update_title(self, tab):
        i = self.tabs.indexOf(tab)
        if i >= 0:
            mark = "○ " if not tab.connected else ("● " if tab.pending else "")
            self.tabs.setTabText(i, mark + tab.name)
            self.tabs.setTabToolTip(i, "Desconectada: usa ⟳ Reconectar" if not tab.connected else "")

    def close_conn(self, i):
        tab = self.tabs.widget(i)
        state = tab.snapshot()
        if tab.close_connection():
            self.tabs.removeTab(i)
            tab.deleteLater()
            self.keeper.park(state)               # sus hojas quedan guardadas para la próxima vez

    def closeEvent(self, event):
        """Al salir se guarda la sesión completa para recuperarla al abrir."""
        full = self.keeper.snapshot()
        closed = []
        while self.tabs.count():
            tab = self.tabs.widget(0)
            state = tab.snapshot()
            if not tab.close_connection():
                for s in closed:                  # se canceló la salida: lo ya cerrado queda guardado
                    self.keeper.park(s)
                event.ignore()
                return
            closed.append(state)
            self.tabs.removeTab(0)
        self.closing = True                             # lo que termine de conectar después ya no abre nada
        self.keeper.stop()
        self.keeper.paused = False                      # se pudo cerrar mientras se recuperaba la sesión
        self.keeper.save(full)
        self.relaunch_exe = self.updates.on_exit()      # actualización pendiente: ya con todo guardado
        event.accept()
