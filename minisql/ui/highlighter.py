"""Colores de sintaxis del editor SQL."""

import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from ..sql.text import SQL_FUNCTIONS, SQL_KEYWORDS, string_end

# Colores (oscuro, claro): suaves y con buen contraste sobre el fondo del tema (ver theme.py)
SQL_COLORS = {
    "keyword": ("#7aa2ff", "#2f5bd3"),
    "function": ("#e0b86b", "#8a5a00"),
    "string": ("#9ece6a", "#2a7a3b"),
    "number": ("#ff9e64", "#b4551f"),
    "comment": ("#6b7489", "#8a93a5"),
    "bind": ("#7dcfff", "#0b7285"),
}


class SqlHighlighter(QSyntaxHighlighter):
    """Colorea palabras clave, funciones, cadenas, números, comentarios y bind variables.
    Estado del bloque: 1 = dentro de /* */, 2 = dentro de una cadena '...'."""
    TOKEN = re.compile(r"(--.*)|(/\*)|(')|(:[A-Za-z_]\w*)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][\w$#]*)")

    def __init__(self, document, dark):
        super().__init__(document)
        self.formats = {}
        for name, (dark_color, light_color) in SQL_COLORS.items():
            f = QTextCharFormat()
            f.setForeground(QColor(dark_color if dark else light_color))
            if name == "keyword":
                f.setFontWeight(QFont.Bold)
            if name == "comment":
                f.setFontItalic(True)
            self.formats[name] = f

    def highlightBlock(self, text):
        n, i = len(text), 0
        state = self.previousBlockState()
        if state == 1:
            end = text.find("*/")
            if end < 0:
                self.setFormat(0, n, self.formats["comment"])
                self.setCurrentBlockState(1)
                return
            i = end + 2
            self.setFormat(0, i, self.formats["comment"])
        elif state == 2:
            end = string_end(text, 0)
            if end < 0:
                self.setFormat(0, n, self.formats["string"])
                self.setCurrentBlockState(2)
                return
            i = end
            self.setFormat(0, i, self.formats["string"])
        self.setCurrentBlockState(0)

        while i < n:
            m = self.TOKEN.search(text, i)
            if not m:
                break
            s = m.start()
            if m.group(1):
                self.setFormat(s, n - s, self.formats["comment"])
                return
            if m.group(2):
                end = text.find("*/", s + 2)
                if end < 0:
                    self.setFormat(s, n - s, self.formats["comment"])
                    self.setCurrentBlockState(1)
                    return
                self.setFormat(s, end + 2 - s, self.formats["comment"])
                i = end + 2
                continue
            if m.group(3):
                end = string_end(text, s + 1)
                if end < 0:
                    self.setFormat(s, n - s, self.formats["string"])
                    self.setCurrentBlockState(2)
                    return
                self.setFormat(s, end - s, self.formats["string"])
                i = end
                continue
            if m.group(4):
                self.setFormat(s, m.end() - s, self.formats["bind"])
            elif m.group(5):
                self.setFormat(s, m.end() - s, self.formats["number"])
            else:
                word = m.group(6).upper()
                called = text[m.end():].lstrip()[:1] == "(" or word in ("SYSDATE", "SYSTIMESTAMP")
                if word in SQL_FUNCTIONS and called:
                    self.setFormat(s, m.end() - s, self.formats["function"])
                elif word in SQL_KEYWORDS:
                    self.setFormat(s, m.end() - s, self.formats["keyword"])
            i = m.end()
