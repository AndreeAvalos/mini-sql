"""Cierre automático de ' ( [ { en el editor de SQL."""
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from minisql.ui.editors import CodeEditor, SqlEditor


def typed(keys, start="", editor_cls=SqlEditor):
    """Escribe keys en un editor que ya tiene start (| marca el cursor) y devuelve el texto con el cursor."""
    ed = editor_cls()
    pos = start.find("|")
    ed.setPlainText(start.replace("|", ""))
    cur = ed.textCursor()
    cur.setPosition(pos if pos >= 0 else len(start))
    ed.setTextCursor(cur)
    for k in keys:
        if k == "\b":
            QTest.keyClick(ed, Qt.Key_Backspace)
        elif k == "\n":                       # keyClicks con un salto de línea tumba a PySide6: Enter como tecla
            QTest.keyClick(ed, Qt.Key_Return)
        else:
            QTest.keyClicks(ed, k)
    text, p = ed.toPlainText(), ed.textCursor().position()
    return text[:p] + "|" + text[p:]


def test_opening_inserts_the_closing(app):
    assert typed("(") == "(|)"
    assert typed("[") == "[|]"
    assert typed("{") == "{|}"
    assert typed("'") == "'|'"
    assert typed("count(") == "count(|)"
    assert typed("where nombre = 'ana") == "WHERE nombre = 'ana|'"


def test_typing_the_closing_steps_over_it(app):
    assert typed("count(*)") == "count(*)|"
    assert typed("'x'") == "'x'|"
    assert typed("in ('a', 'b')") == "IN ('a', 'b')|"
    assert typed("[1]") == "[1]|"


def test_backspace_removes_an_empty_pair(app):
    assert typed("(\b") == "|"
    assert typed("'\b") == "|"
    assert typed("\b", "x(|)") == "x|"
    assert typed("\b", "(a|)") == "(|)"                         # con algo adentro, Retroceso normal


def test_no_pair_inside_words_or_texts(app):
    assert typed("(", "|nombre") == "(|nombre"                  # antes de una palabra: solo el (
    assert typed("'", "o|") == "o'|"                            # O'Brien
    assert typed("(", "'ab|'") == "'ab(|'"                      # dentro de un texto, tal cual
    assert typed("'", "'it|'") == "'it'|"                       # cierra el texto: pasa por encima


def test_selection_is_wrapped(app):
    ed = SqlEditor()
    ed.setPlainText("select a from t")
    cur = ed.textCursor()
    cur.setPosition(7)
    cur.setPosition(8, cur.MoveMode.KeepAnchor)
    ed.setTextCursor(cur)
    QTest.keyClicks(ed, "(")
    assert ed.toPlainText() == "select (a) from t" and ed.textCursor().selectedText() == "a"
    ed.undo()
    assert ed.toPlainText() == "select a from t"                # un solo paso de deshacer


def test_keywords_become_uppercase_when_finished(app):
    assert typed("select * from emp where a = 1 and b in (1)") == "SELECT * FROM emp WHERE a = 1 AND b IN (1)|"
    assert typed("select\n") == "SELECT\n|"
    assert typed("selec") == "selec|"                                       # a medias: todavía no
    assert typed("e.date,") == "e.date,|"                                   # alias.columna: no
    assert typed(":from ") == ":from |"                                     # bind: no
    assert typed("where x = 'select from' ") == "WHERE x = 'select from' |"  # dentro de un texto: no
    assert typed("-- select from\n") == "-- select from\n|"                 # en un comentario: no
    assert typed("count(") == "count(|)"                                    # funciones: como se escriban


def test_code_editor_too_but_not_read_only(app):
    assert typed("(", editor_cls=CodeEditor) == "(|)"
    ed = SqlEditor()
    ed.setReadOnly(True)
    QTest.keyClicks(ed, "(")
    assert ed.toPlainText() == ""
