"""Colores de sintaxis del editor SQL."""

import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from ..sql.text import SQL_FUNCTIONS, SQL_KEYWORDS, string_end

# Colores (oscuro, claro): estilo VS Code
SQL_COLORS = {
    "keyword": ("#569CD6", "#0000FF"),
    "function": ("#DCDCAA", "#795E26"),
    "string": ("#CE9178", "#A31515"),
    "number": ("#B5CEA8", "#098658"),
    "comment": ("#6A9955", "#008000"),
    "bind": ("#9CDCFE", "#001080"),
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
