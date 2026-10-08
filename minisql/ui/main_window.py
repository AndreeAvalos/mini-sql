"""Ventana principal."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QMainWindow, QTabWidget, QToolButton

from .connect_dialog import ConnectDialog
from .connection_tab import ConnectionTab
from .session_keeper import SessionKeeper


class MainWindow(QMainWindow):
    def __init__(self, session_path=None, restore=True):
        super().__init__()
        self.setWindowTitle("MiniSQL")
        self.resize(1200, 800)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_conn)
        btn = QToolButton()
        btn.setText("+ Conexión")
        btn.setToolTip("Nueva conexión (Ctrl+N)")
        btn.clicked.connect(self.new_connection)
        self.tabs.setCornerWidget(btn, Qt.TopRightCorner)
        self.setCentralWidget(self.tabs)

        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self.new_connection)
        QShortcut(QKeySequence("Ctrl+T"), self).activated.connect(self.new_sheet)
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(self.close_current_sheet)

        self.keeper = SessionKeeper(self, session_path)
        if restore:
            QTimer.singleShot(0, self.restore_session)   # el guardián arranca al terminar de recuperar
        else:
            self.keeper.start()

    def conn_tabs(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count())
                if isinstance(self.tabs.widget(i), ConnectionTab)]

    def restore_session(self):
        """Al abrir: vuelve a conectar y abrir las hojas de la sesión anterior."""
        states = self.keeper.saved.get("connections") or []
        if not states:
            self.keeper.start()
            self.new_connection()
            return
        self.keeper.paused = True
        for state in states:
            dlg = ConnectDialog(self, preset=state)
            if dlg.exec() == QDialog.Accepted:
                tab = self.add_connection(dlg.session, dlg.conn_name, dlg.dsn_label)
                if dlg.conn_name == state["name"]:
                    tab.restore(state)
                else:
                    self.keeper.park(state)
            else:
                self.keeper.park(state)               # no se pierde: vuelve al conectarse a ese perfil
        self.keeper.paused = False
        self.keeper.save()
        self.keeper.start()
        if self.keeper.parked:
            names = ", ".join(sorted(self.keeper.parked))
            self.statusBar().showMessage(
                f"Hojas guardadas de {names}: se recuperan al conectarte a ese perfil.", 15000)

    def add_connection(self, session, name, dsn_label=None):
        tab = ConnectionTab(session, name, dsn_label)
        idx = self.tabs.addTab(tab, name)
        tab.pending_changed.connect(lambda t=tab: self.update_title(t))
        self.tabs.setCurrentIndex(idx)
        parked = self.keeper.claim(name)
        if parked:
            tab.restore(parked)
            self.statusBar().showMessage(f"Se recuperaron las hojas guardadas de {name}.", 10000)
        return tab

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
            self.tabs.setTabText(i, ("● " if tab.pending else "") + tab.name)

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
        self.keeper.stop()
        self.keeper.save(full)
        event.accept()
