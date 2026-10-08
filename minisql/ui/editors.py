"""Editores de texto: SQL con autocompletado y código PL/SQL con margen y breakpoints."""

import re

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QPainter,
    QPolygon,
    QShortcut,
    QStandardItem,
    QStandardItemModel,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import QCompleter, QPlainTextEdit, QTextEdit, QToolTip, QWidget

from ..sql.completion import completion_target, make_alias, table_refs, wants_alias
from ..sql.text import inside_literal, q, statement_range, word_at
from .highlighter import SqlHighlighter
from .style import is_dark, mono_font


class SqlEditor(QPlainTextEdit):
    """Editor de SQL: fuente monoespaciada, colores de sintaxis y autocompletado (aparece al escribir
    2 letras o un punto, o con Ctrl+Espacio). Sin provider no autocompleta."""
    NAME_ROLE = Qt.UserRole + 1
    IDENT_ROLE = Qt.UserRole + 2
    LOWER_ROLE = Qt.UserRole + 3
    TABLE_ROLE = Qt.UserRole + 4
    QUAL_ROLE = Qt.UserRole + 5
    TABLE_KINDS = frozenset({"tabla", "vista", "vista mat.", "sinónimo"})
    MIN_PREFIX = 2

    def __init__(self, provider=None):
        super().__init__()
        self.setFont(mono_font())
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self.dark = is_dark(self)
        self.highlighter = SqlHighlighter(self.document(), self.dark)
        self.provider = provider
        self.prefix = ""
        self.alias_taken = None      # alias ya usados si se está escribiendo una tabla en FROM/JOIN
        self.waiting = None          # None, o True/False (manual) si esperamos metadatos
        self.cmodel = QStandardItemModel(self)
        self.completer = QCompleter(self.cmodel, self)
        self.completer.setWidget(self)
        self.completer.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
        self.completer.setMaxVisibleItems(12)
        self.completer.activated[QModelIndex].connect(self.insert_completion)
        if provider is not None:
            provider.updated.connect(self._on_metadata)

    def _on_metadata(self):
        if self.waiting is not None and self.hasFocus():
            self.show_completions(self.waiting)

    def keyPressEvent(self, e):
        popup = self.completer.popup()
        if popup.isVisible() and e.key() in (Qt.Key_Enter, Qt.Key_Return, Qt.Key_Tab,
                                             Qt.Key_Backtab, Qt.Key_Escape):
            e.ignore()               # el QCompleter acepta o cierra la sugerencia
            return
        if e.key() == Qt.Key_Space and e.modifiers() & Qt.ControlModifier:
            if self.provider is not None:
                self.show_completions(manual=True)
            return
        super().keyPressEvent(e)
        if self.provider is None or e.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            return
        text = e.text()
        typed_word = bool(text) and (text[-1].isalnum() or text[-1] in "_$#.")
        erased = popup.isVisible() and e.key() in (Qt.Key_Backspace, Qt.Key_Delete)
        if typed_word or erased:
            self.show_completions(manual=False)
        elif text or e.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Home, Qt.Key_End):
            self.hide_completions()

    def hide_completions(self):
        self.waiting = None
        self.completer.popup().hide()
        self.cmodel.clear()

    def show_completions(self, manual):
        text = self.toPlainText()
        pos = self.textCursor().position()
        start, end = statement_range(text, pos)
        before = text[start:pos]
        quals, prefix = completion_target(before)
        rest = before[:len(before) - len(prefix)]
        if (inside_literal(before) or prefix[:1].isdigit() or rest.endswith(":")
                or (not manual and not quals and len(prefix) < self.MIN_PREFIX)):
            self.hide_completions()
            return
        self.prefix, items = self.provider.suggest(text[start:end], before)
        if wants_alias(before):
            refs = table_refs(text[start:pos - len(prefix)] + text[pos:end])   # sin la palabra a medias
            self.alias_taken = set(refs) | {name for _owner, name in refs.values()}
        else:
            self.alias_taken = None
        if not manual and all(name.upper() == prefix.upper() and not qual for name, _d, _i, qual in items):
            items = []               # ya está escrito completo: no estorbar
        self.waiting = manual if self.provider.busy else None
        if not items:
            self.completer.popup().hide()
            self.cmodel.clear()
            return
        shown = [(f"{qual}.{name}" if qual else name) for name, _d, _i, qual in items]
        width = min(max(len(s) for s in shown), 40)
        lower = self._prefers_lower(before)
        self.cmodel.clear()
        for label, (name, detail, ident, qual) in zip(shown, items, strict=True):
            it = QStandardItem(f"{label.ljust(width)}  {detail}")
            it.setData(name, self.NAME_ROLE)
            it.setData(qual, self.QUAL_ROLE)
            it.setData(ident, self.IDENT_ROLE)
            it.setData(lower, self.LOWER_ROLE)
            it.setData(ident and detail in self.TABLE_KINDS, self.TABLE_ROLE)
            it.setEditable(False)
            self.cmodel.appendRow(it)
        popup = self.completer.popup()
        popup.setFont(self.font())
        rect = self.cursorRect()
        rect.setWidth(popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width() + 12)
        self.completer.complete(rect)
        popup.setCurrentIndex(self.cmodel.index(0, 0))

    @staticmethod
    def _prefers_lower(before):
        """Si el usuario escribe en minúsculas, insertar en minúsculas."""
        m = re.search(r"([A-Za-z_][\w$#]*)\.?[\w$#]*$", before)
        word = re.search(r"[A-Za-z]+[\w$#]*$", before)
        sample = (word.group() if word else "") or (m.group(1) if m else "")
        return bool(sample) and sample.islower()

    def insert_completion(self, index):
        name = index.data(self.NAME_ROLE)
        if not name:
            return
        if index.data(self.IDENT_ROLE):
            name = q(name)
        if index.data(self.LOWER_ROLE) and not name.startswith('"'):
            name = name.lower()
        if index.data(self.QUAL_ROLE):
            name = f"{index.data(self.QUAL_ROLE)}.{name}"     # where fec -> where e.fecha
        cur = self.textCursor()
        after = self.toPlainText()[cur.position():cur.position() + 1]
        if self.alias_taken is not None and index.data(self.TABLE_ROLE) and not re.match(r"[\w$#.\"]", after):
            # FROM CLIENTE_DIRECCION cd  -> luego cd. sugiere sus columnas
            name += " " + make_alias(index.data(self.NAME_ROLE), self.alias_taken)
        cur.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(self.prefix))
        cur.insertText(name)
        self.setTextCursor(cur)
        self.waiting = None


class GutterArea(QWidget):
    """Margen con números de línea y puntos de interrupción del CodeEditor."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def paintEvent(self, event):
        self.editor.paint_gutter(event)

    def mousePressEvent(self, event):
        cur = self.editor.cursorForPosition(QPoint(0, int(event.position().y())))
        self.editor.toggle_breakpoint(cur.blockNumber() + 1)


class CodeEditor(SqlEditor):
    """Editor de código PL/SQL: números de línea, puntos de interrupción (clic en el margen o F9)
    y la línea donde está detenido el depurador."""
    breakpoint_toggled = Signal(int, bool)

    def __init__(self, provider=None):
        super().__init__(provider)
        self.breakpoints = set()
        self.exec_line = None
        self.debug_values = {}         # NOMBRE -> valor, mientras el depurador está en pausa
        self.gutter = GutterArea(self)
        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._update_gutter)
        self._update_margin()
        sc = QShortcut(QKeySequence("F9"), self)
        sc.setContext(Qt.WidgetShortcut)
        sc.activated.connect(lambda: self.toggle_breakpoint())

    def gutter_width(self):
        digits = max(3, len(str(self.blockCount())))
        return 24 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_margin(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event):
        painter = QPainter(self.gutter)
        painter.fillRect(event.rect(), QColor("#252526" if self.dark else "#f3f3f3"))
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        h = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            bottom = top + round(self.blockBoundingRect(block).height())
            if block.isVisible() and bottom >= event.rect().top():
                line = block.blockNumber() + 1
                if line in self.breakpoints:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor("#e51400"))
                    painter.drawEllipse(4, top + (h - 10) // 2, 10, 10)
                if line == self.exec_line:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor("#ffcc00"))
                    y = top + h // 2
                    painter.drawPolygon(QPolygon([QPoint(3, y - 5), QPoint(13, y), QPoint(3, y + 5)]))
                painter.setPen(QColor("#858585"))
                painter.drawText(0, top, self.gutter.width() - 4, h, Qt.AlignRight | Qt.AlignVCenter, str(line))
            block = block.next()
            top = bottom
        painter.end()

    def set_debug_values(self, values):
        """Valores de las variables en la pausa actual (se ven al pasar el mouse); {} al seguir o terminar."""
        self.debug_values = {name.upper(): value for name, value in values}

    def value_at(self, pos):
        """(nombre, valor) de la variable bajo la posición del viewport, o None."""
        cur = self.cursorForPosition(pos)
        block = cur.block()
        if not self.debug_values or not self.cursorRect(cur).adjusted(-20, -4, 20, 4).contains(pos):
            return None
        name = word_at(block.text(), cur.positionInBlock())
        if not name and cur.positionInBlock():
            name = word_at(block.text(), cur.positionInBlock() - 1)   # el mouse sobre la última letra
        value = self.debug_values.get(name.upper())
        return (name, value) if value is not None else None

    def viewportEvent(self, event):
        if event.type() == QEvent.ToolTip:
            found = self.value_at(event.pos())
            if found:
                QToolTip.showText(event.globalPos(), f"{found[0]} = {found[1]}", self.viewport())
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(event)

    def toggle_breakpoint(self, line=None):
        line = line or self.textCursor().blockNumber() + 1
        added = line not in self.breakpoints
        self.breakpoints ^= {line}
        self.gutter.update()
        self.breakpoint_toggled.emit(line, added)

    def set_exec_line(self, line):
        """Marca (o quita, con None) la línea donde está detenido el depurador."""
        if line is None and self.exec_line is not None and self.textCursor().hasSelection():
            cur = self.textCursor()
            cur.clearSelection()                  # quitar también la selección que puso la pausa
            self.setTextCursor(cur)
        self.exec_line = line
        sels = []
        if line:
            block = self.document().findBlockByNumber(line - 1)
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor("#4b4700" if self.dark else "#fff3a0"))
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sel.cursor = QTextCursor(block)
            sels.append(sel)
            # la línea queda seleccionada (sin la sangría) y el editor con el foco para que se vea
            text = block.text()
            cur = QTextCursor(block)
            cur.setPosition(block.position() + len(text) - len(text.lstrip()))
            cur.setPosition(block.position() + len(text.rstrip()), QTextCursor.KeepAnchor)
            self.setTextCursor(cur)
            self.centerCursor()
            self.setFocus()
        self.setExtraSelections(sels)
        self.gutter.update()

    def go_to_line(self, line, column=1):
        block = self.document().findBlockByNumber(max(0, line - 1))
        cur = QTextCursor(block)
        cur.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, max(0, min(column - 1, block.length() - 1)))
        self.setTextCursor(cur)
        self.centerCursor()
        self.setFocus()
