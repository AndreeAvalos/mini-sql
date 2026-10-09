"""Buscar y reemplazar: la lógica (sql/search.py) y la barra de los editores (ui/find_bar.py)."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from minisql.sql.search import QtPositions, SearchOptions, find_matches, replace_all
from minisql.ui.connection_tab import ConnectionTab
from minisql.ui.editors import CodeEditor
from minisql.ui.find_bar import with_find_bar

from support import FakeConn, make_session, pump, wait_until

SQL = "select emp_id, nombre\nfrom EMP e\nwhere e.emp_id = :emp and v$emp = 'Emp'"


# ---------------------------------------------------------------- lógica

def test_find_options():
    assert len(find_matches(SQL, "emp")) == 6                                   # sin distinguir mayúsculas
    assert len(find_matches(SQL, "EMP", SearchOptions(case=True))) == 1
    words = find_matches(SQL, "emp", SearchOptions(whole_word=True))
    assert [SQL[s:e] for s, e in words] == ["EMP", "emp", "Emp"]                # no emp_id, v$emp ni :emp…
    assert SQL[slice(*words[1])] == "emp" and SQL[words[1][0] - 1] == ":"         # :emp sí es palabra completa
    assert find_matches(SQL, "") == []
    assert find_matches(SQL, "^", SearchOptions(regex=True)) == []              # coincidencias vacías: no
    with pytest.raises(ValueError, match="Expresión regular inválida"):
        find_matches(SQL, "(emp", SearchOptions(regex=True))


def test_replace_all_with_regex_groups():
    text, n = replace_all("a.id = b.id and c.id = d.id", r"(\w)\.id", r"\1.codigo", SearchOptions(regex=True))
    assert (text, n) == ("a.codigo = b.codigo and c.codigo = d.codigo", 4)
    assert replace_all("EMP emp Emp", "emp", "x") == ("x x x", 3)
    assert replace_all("a\\1", "a", r"\1") == ("\\1\\1", 1)                    # sin regex, el texto es literal


def test_qt_positions_with_emoji():
    text = "-- 🔑 clave\nselect emp"
    pos = QtPositions(text)
    i = text.index("emp")
    assert pos(i) == i + 1                         # el emoji ocupa 2 posiciones en Qt
    assert pos.to_python(pos(i)) == i
    assert QtPositions("sin emojis")(4) == 4


# ---------------------------------------------------------------- barra en las hojas

def sheet_with_text(app, text=SQL):
    tab = ConnectionTab(make_session(FakeConn()), "T", "PROD")
    tab.resize(900, 600)
    tab.show()
    sheet = tab.current_sheet()
    sheet.editor.setPlainText(text)
    pump(app, 5)
    return tab, sheet


def found_texts(editor):
    return [s.cursor.selectedText() for s in editor.highlight_layers.get("find", [])
            + editor.highlight_layers.get("find_current", [])]


def test_ctrl_f_searches_while_typing(app):
    _tab, sheet = sheet_with_text(app)
    editor, bar = sheet.editor, sheet.find_bar
    editor.setFocus()
    QTest.keyClick(editor, Qt.Key_F, Qt.ControlModifier)          # el atajo de verdad
    assert bar.isVisible() and bar.find_edit.hasFocus()
    QTest.keyClicks(bar.find_edit, "emp")
    assert bar.count.text() == "1 de 6"
    assert editor.textCursor().selectedText() == "emp"
    assert len(found_texts(editor)) == 6                          # todas resaltadas
    QTest.keyClick(bar.find_edit, Qt.Key_Return)                  # Enter: siguiente
    assert bar.count.text() == "2 de 6" and editor.textCursor().selectedText() == "EMP"
    QTest.keyClick(bar.find_edit, Qt.Key_Return, Qt.ShiftModifier)
    assert bar.count.text() == "1 de 6"
    QTest.keyClick(bar.find_edit, Qt.Key_Return, Qt.ShiftModifier)    # antes de la primera: da la vuelta
    assert bar.count.text().startswith("6 de 6") and "vuelta" in bar.count.text()
    bar.word.setChecked(True)
    assert bar.count.text().endswith("3") and len(found_texts(editor)) == 3
    QTest.keyClick(bar.find_edit, Qt.Key_Escape)                  # Esc cierra y limpia
    assert not bar.isVisible() and found_texts(editor) == [] and editor.hasFocus()


def test_selected_text_is_searched(app):
    _tab, sheet = sheet_with_text(app)
    cursor = sheet.editor.textCursor()
    cursor.setPosition(SQL.index("nombre"))
    cursor.setPosition(SQL.index("nombre") + len("nombre"), cursor.MoveMode.KeepAnchor)
    sheet.editor.setTextCursor(cursor)
    sheet.find_bar.open()
    assert sheet.find_bar.find_edit.text() == "nombre" and sheet.find_bar.count.text() == "1 de 1"


def test_replace_one_and_all_with_single_undo(app):
    _tab, sheet = sheet_with_text(app)
    editor, bar = sheet.editor, sheet.find_bar
    bar.open(replace=True)
    assert bar.replace_row.isVisible()
    bar.word.setChecked(True)
    bar.find_edit.setText("emp")
    bar.replace_edit.setText("empleado")
    bar.replace_current()                                          # reemplaza "EMP" y pasa a la siguiente
    assert "from empleado e" in editor.toPlainText() and bar.count.text() == "1 de 2"
    bar.replace_all()
    assert editor.toPlainText() == SQL.replace("EMP e", "empleado e").replace(":emp", ":empleado") \
        .replace("'Emp'", "'empleado'")
    assert bar.count.text() == "2 reemplazos"
    editor.undo()                                                  # un solo Ctrl+Z deshace "Reemplazar todo"
    assert "from empleado e" in editor.toPlainText() and ":emp " in editor.toPlainText()
    editor.undo()
    assert editor.toPlainText() == SQL


def test_invalid_regex_is_reported(app):
    _tab, sheet = sheet_with_text(app)
    bar = sheet.find_bar
    bar.open()
    bar.regex.setChecked(True)
    bar.find_edit.setText("(emp")
    assert "inválida" in bar.count.text() and bar.matches == []


def test_highlights_follow_edits_while_open(app):
    _tab, sheet = sheet_with_text(app)
    bar = sheet.find_bar
    bar.open()
    bar.find_edit.setText("nombre")
    assert len(bar.matches) == 1
    sheet.editor.appendPlainText("-- otra vez nombre")
    assert wait_until(app, lambda: len(bar.matches) == 2)


# ---------------------------------------------------------------- código y DDL

def test_find_in_read_only_ddl_has_no_replace(app):
    editor = CodeEditor()
    editor.setReadOnly(True)
    box = with_find_bar(editor)
    box.show()
    editor.setPlainText("CREATE TABLE EMP (ID NUMBER)")
    box.find_bar.open(replace=True)
    assert not box.find_bar.replace_row.isVisible() and not box.find_bar.toggle_replace.isVisible()
    box.find_bar.find_edit.setText("number")
    assert box.find_bar.count.text() == "1 de 1"
    box.find_bar.replace_all()                                     # no hace nada en solo lectura
    assert editor.toPlainText() == "CREATE TABLE EMP (ID NUMBER)"


def test_search_does_not_erase_the_debugger_line(app):
    editor = CodeEditor()
    box = with_find_bar(editor)
    box.show()
    editor.setPlainText("BEGIN\n  v := 1;\n  v := 2;\nEND;")
    editor.set_exec_line(2)
    box.find_bar.open()
    box.find_bar.find_edit.setText("v")
    layers = editor.highlight_layers
    assert len(layers["exec"]) == 1 and len(layers["find"]) + len(layers["find_current"]) == 2
    assert len(editor.extraSelections()) == 3                     # las dos capas a la vez
    box.find_bar.close_bar()
    assert len(editor.extraSelections()) == 1 and editor.exec_line == 2
