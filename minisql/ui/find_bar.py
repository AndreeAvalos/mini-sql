"""Buscar y reemplazar en un editor: barra debajo del editor, como en VS Code o SQL Developer.

    Ctrl+F buscar · Ctrl+H reemplazar · Enter/F3 siguiente · Shift+Enter/Shift+F3 anterior · Esc cerrar
"""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..sql.search import QtPositions, SearchOptions, find_matches, replacement_for
from .style import ERROR_COLOR, MUTED_COLOR

MAX_HIGHLIGHTS = 5000        # en hojas enormes se cuentan todas, pero se pintan como máximo estas


class _SearchEdit(QLineEdit):
    """Caja de texto donde Enter va al siguiente y Shift+Enter al anterior."""
    next_requested = Signal()
    previous_requested = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            (self.previous_requested if event.modifiers() & Qt.ShiftModifier else self.next_requested).emit()
            return
        super().keyPressEvent(event)


def _toggle(text, tip):
    button = QToolButton()
    button.setText(text)
    button.setToolTip(tip)
    button.setCheckable(True)
    button.setAutoRaise(True)
    return button


class FindBar(QWidget):
    """Barra de buscar/reemplazar de un editor. Resalta todas las coincidencias y la actual."""
    visibility_changed = Signal(bool)

    def showEvent(self, event):
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.matches = []            # [(inicio, fin)] en índices de Python
        self.positions = QtPositions("")
        self.current = -1            # índice en matches de la coincidencia seleccionada

        self.find_edit = _SearchEdit()
        self.find_edit.setPlaceholderText("Buscar")
        self.find_edit.setClearButtonEnabled(True)
        self.case = _toggle("Aa", "Distinguir mayúsculas")
        self.word = _toggle("ab", "Solo palabras completas")
        self.regex = _toggle(".*", "Expresión regular")
        self.count = QLabel("")
        self.count.setMinimumWidth(110)
        prev_btn = QToolButton()
        prev_btn.setText("↑")
        prev_btn.setToolTip("Anterior (Shift+Enter / Shift+F3)")
        next_btn = QToolButton()
        next_btn.setText("↓")
        next_btn.setToolTip("Siguiente (Enter / F3)")
        self.toggle_replace = QToolButton()
        self.toggle_replace.setText("⇄")
        self.toggle_replace.setToolTip("Mostrar reemplazo (Ctrl+H)")
        self.toggle_replace.setCheckable(True)
        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setToolTip("Cerrar (Esc)")
        close_btn.setAutoRaise(True)

        self.replace_edit = _SearchEdit()
        self.replace_edit.setPlaceholderText("Reemplazar con  (con .* se puede usar \\1, \\2…)")
        self.replace_btn = QToolButton()
        self.replace_btn.setText("Reemplazar")
        self.replace_btn.setToolTip("Reemplaza la coincidencia actual y pasa a la siguiente")
        self.replace_all_btn = QToolButton()
        self.replace_all_btn.setText("Reemplazar todo")
        self.replace_all_btn.setToolTip("Reemplaza todas (se deshace con un solo Ctrl+Z)")

        find_row = QHBoxLayout()
        find_row.setContentsMargins(0, 0, 0, 0)
        find_row.setSpacing(2)
        for w in (self.toggle_replace, self.find_edit, self.case, self.word, self.regex, self.count,
                  prev_btn, next_btn, close_btn):
            find_row.addWidget(w, 1 if w is self.find_edit else 0)
        self.replace_row = QWidget()
        replace_layout = QHBoxLayout(self.replace_row)
        replace_layout.setContentsMargins(self.toggle_replace.sizeHint().width() + 2, 0, 0, 0)
        replace_layout.setSpacing(2)
        replace_layout.addWidget(self.replace_edit, 1)
        replace_layout.addWidget(self.replace_btn)
        replace_layout.addWidget(self.replace_all_btn)
        replace_layout.addStretch(0)
        self.replace_row.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(2)
        layout.addLayout(find_row)
        layout.addWidget(self.replace_row)
        self.hide()

        # al editar el documento con la barra abierta, se recalcula (con pausa, por las hojas grandes)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(150)
        self.refresh_timer.timeout.connect(lambda: self.refresh(select=False))

        self.find_edit.textChanged.connect(lambda _t: self.refresh(select=True))
        for option in (self.case, self.word, self.regex):
            option.toggled.connect(lambda _on: self.refresh(select=True))
        self.find_edit.next_requested.connect(self.find_next)
        self.find_edit.previous_requested.connect(self.find_previous)
        self.replace_edit.next_requested.connect(self.replace_current)
        prev_btn.clicked.connect(self.find_previous)
        next_btn.clicked.connect(self.find_next)
        close_btn.clicked.connect(self.close_bar)
        self.toggle_replace.toggled.connect(self._show_replace_row)
        self.replace_btn.clicked.connect(self.replace_current)
        self.replace_all_btn.clicked.connect(self.replace_all)
        editor.textChanged.connect(self._document_changed)

    # --- abrir / cerrar
    @property
    def options(self):
        return SearchOptions(case=self.case.isChecked(), whole_word=self.word.isChecked(),
                             regex=self.regex.isChecked())

    def open(self, replace=False):
        """Ctrl+F (o Ctrl+H con replace=True). Si hay texto seleccionado de una línea, se busca ese."""
        selected = self.editor.textCursor().selectedText()
        if selected and "\u2029" not in selected:
            self.find_edit.blockSignals(True)
            self.find_edit.setText(selected)
            self.find_edit.blockSignals(False)
        can_replace = not self.editor.isReadOnly()
        self.toggle_replace.setVisible(can_replace)
        self.toggle_replace.setChecked(replace and can_replace)
        self._show_replace_row(replace and can_replace)
        self.show()
        (self.replace_edit if replace and can_replace and self.find_edit.text() else self.find_edit).setFocus()
        self.find_edit.selectAll()
        self.refresh(select=False)

    def close_bar(self):
        self.hide()
        self.matches, self.current = [], -1
        self.editor.set_highlights("find", [])
        self.editor.set_highlights("find_current", [])
        self.editor.setFocus()

    def _show_replace_row(self, on):
        self.replace_row.setVisible(bool(on) and not self.editor.isReadOnly())

    def _document_changed(self):
        if self.isVisible():
            self.refresh_timer.start()

    # --- buscar
    def refresh(self, select):
        """Recalcula las coincidencias. Con select=True salta a la primera desde el cursor (búsqueda
        mientras se escribe); si no, solo actualiza resaltados y contador."""
        text = self.editor.toPlainText()
        self.positions = QtPositions(text)
        self.current = -1
        try:
            self.matches = find_matches(text, self.find_edit.text(), self.options)
        except ValueError as e:
            self.matches = []
            self._paint()
            self._status(str(e), ERROR_COLOR)
            return
        if select and self.matches:
            cursor = self.editor.textCursor()
            self._select(self._index_from(self.positions.to_python(cursor.selectionStart())))
        else:
            self.current = self._index_of_selection()
            self._paint()
            self._update_count()

    def find_next(self):
        if not self._ensure_matches():
            return
        cursor = self.editor.textCursor()
        start = self.positions.to_python(cursor.selectionEnd() if cursor.hasSelection() else cursor.position())
        self._select(self._index_from(start), wrapped_hint=True)

    def find_previous(self):
        if not self._ensure_matches():
            return
        start = self.positions.to_python(self.editor.textCursor().selectionStart())
        before = [i for i, (s, _e) in enumerate(self.matches) if s < start]
        self._select(before[-1] if before else len(self.matches) - 1, wrapped_hint=not before)

    def _ensure_matches(self):
        if not self.isVisible():
            self.open()
        if not self.matches:
            self.refresh(select=False)
        return bool(self.matches)

    def _index_from(self, start):
        """Primera coincidencia que empieza en `start` o después (da la vuelta al final)."""
        for i, (s, _e) in enumerate(self.matches):
            if s >= start:
                return i
        return 0

    def _index_of_selection(self):
        cursor = self.editor.textCursor()
        span = (self.positions.to_python(cursor.selectionStart()), self.positions.to_python(cursor.selectionEnd()))
        return self.matches.index(span) if span in self.matches else -1

    def _select(self, index, wrapped_hint=False):
        previous = self.current
        self.current = index
        start, end = self.matches[index]
        cursor = self.editor.textCursor()
        cursor.setPosition(self.positions(start))
        cursor.setPosition(self.positions(end), QTextCursor.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self.editor.centerCursor()
        self._paint()
        wrapped = wrapped_hint and previous >= 0 and len(self.matches) > 1 and (
            (index == 0 and previous == len(self.matches) - 1) or (index == len(self.matches) - 1 and previous == 0))
        self._update_count("  · se dio la vuelta" if wrapped else "")

    # --- reemplazar
    def replace_current(self):
        """Reemplaza la coincidencia seleccionada (si lo está) y pasa a la siguiente."""
        if self.editor.isReadOnly() or not self._ensure_matches():
            return
        index = self._index_of_selection()
        if index < 0:
            self.find_next()
            return
        text = self.editor.toPlainText()
        try:
            new = replacement_for(text, self.matches[index], self.find_edit.text(), self.replace_edit.text(),
                                  self.options)
        except ValueError as e:
            self._status(str(e), ERROR_COLOR)
            return
        cursor = self.editor.textCursor()
        cursor.insertText(new)
        self.editor.setTextCursor(cursor)
        self.refresh(select=False)
        if self.matches:
            self.find_next()

    def replace_all(self):
        """Reemplaza todas en un solo paso de deshacer (Ctrl+Z las devuelve todas)."""
        if self.editor.isReadOnly():
            return
        self.refresh(select=False)
        if not self.matches:
            return
        text = self.editor.toPlainText()
        try:
            news = [replacement_for(text, span, self.find_edit.text(), self.replace_edit.text(), self.options)
                    for span in self.matches]
        except ValueError as e:
            self._status(str(e), ERROR_COLOR)
            return
        cursor = QTextCursor(self.editor.document())
        cursor.beginEditBlock()
        for (start, end), new in zip(reversed(self.matches), reversed(news), strict=True):  # del final al inicio
            cursor.setPosition(self.positions(start))
            cursor.setPosition(self.positions(end), QTextCursor.KeepAnchor)
            cursor.insertText(new)
        cursor.endEditBlock()
        count = len(news)
        self.refresh(select=False)
        self._status(f"{count} reemplazo{'s' if count != 1 else ''}")

    # --- pintar y contar
    def _paint(self):
        color = QColor("#613214" if self.editor.dark else "#ffd27a")
        current_color = QColor("#a35a00" if self.editor.dark else "#ff9632")
        found = []
        for i, (start, end) in enumerate(self.matches[:MAX_HIGHLIGHTS]):
            if i != self.current:
                found.append(self._selection(start, end, color))
        current = [self._selection(*self.matches[self.current], current_color)] if self.current >= 0 else []
        self.editor.set_highlights("find", found)
        self.editor.set_highlights("find_current", current)

    def _selection(self, start, end, color):
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(color)
        sel.cursor = QTextCursor(self.editor.document())
        sel.cursor.setPosition(self.positions(start))
        sel.cursor.setPosition(self.positions(end), QTextCursor.KeepAnchor)
        return sel

    def _update_count(self, extra=""):
        if not self.find_edit.text():
            self._status("")
        elif not self.matches:
            self._status("Sin resultados", ERROR_COLOR)
        elif self.current >= 0:
            self._status(f"{self.current + 1} de {len(self.matches)}{extra}")
        else:
            self._status(f"{len(self.matches)} resultado{'s' if len(self.matches) != 1 else ''}")

    def _status(self, text, color=MUTED_COLOR):
        self.count.setText(text)
        self.count.setStyleSheet(f"color: {color};")


def with_find_bar(editor):
    """El editor con su barra de búsqueda debajo y los atajos. Devuelve el contenedor (para ponerlo
    donde iba el editor); la barra queda en contenedor.find_bar."""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(editor, 1)
    bar = FindBar(editor)
    layout.addWidget(bar)
    container.find_bar = bar

    def shortcut(keys, slot):
        sc = QShortcut(QKeySequence(keys), container)
        sc.setContext(Qt.WidgetWithChildrenShortcut)
        sc.activated.connect(slot)
        return sc

    shortcut("Ctrl+F", lambda: bar.open(replace=False))
    shortcut("Ctrl+H", lambda: bar.open(replace=True))
    shortcut("F3", bar.find_next)
    shortcut("Shift+F3", bar.find_previous)
    escape = shortcut("Escape", bar.close_bar)
    escape.setEnabled(False)                     # Esc solo cierra la barra cuando está abierta
    bar.visibility_changed.connect(escape.setEnabled)
    return container
