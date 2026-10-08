"""Depuración PL/SQL."""

from minisql.ui.connection_tab import ConnectionTab
from minisql.ui.editors import CodeEditor

from support import FakeConn, FakeDebugSession, make_session, wait_until


def test_debug_session_flow(app):
    tab = ConnectionTab(make_session(FakeConn(), opener=FakeConn), "T", "PROD")
    tab.debugger.session_factory = FakeDebugSession
    viewer = tab.open_object("SCOTT", "PKG", "PACKAGE")
    code = viewer.pages["Código"]
    assert wait_until(app, lambda: "PACKAGE BODY" in code.toPlainText())
    code.toggle_breakpoint(9)                     # "NULL;" = línea 4 del cuerpo
    FakeDebugSession.script = [4, 5, None]
    tab.start_debug("BEGIN SCOTT.PKG.ALTA; END;", [("PACKAGE", "SCOTT", "PKG")])
    panel = tab.debug_panel
    assert panel.isVisibleTo(tab)
    assert wait_until(app, lambda: "línea 4" in panel.location.text())
    assert code.exec_line == 9
    assert panel.vars.model().rows == [("V_X", "42")]
    calls = FakeDebugSession.last.calls
    assert ("compile", "PACKAGE", "SCOTT", "PKG") in calls and ("attach", "SID-1") in calls
    assert ("bp", "PACKAGE BODY", "SCOTT", "PKG", 4) in calls
    assert ("step", "continue") in calls                   # hay breakpoint: corre hasta él
    assert code.textCursor().selectedText() == "NULL;"          # la línea detenida queda seleccionada
    panel.do("over")
    assert wait_until(app, lambda: code.exec_line == 10)
    panel.do("continue")
    # el hilo marca inactive un instante antes de que la interfaz procese "terminado": esperar al panel
    assert wait_until(app, lambda: panel.location.text() == "Terminado")
    assert not tab.debugger.active and code.exec_line is None
    assert ("detach",) in FakeDebugSession.last.calls
    assert not tab.db.busy


def test_debug_stop_before_start(app):
    tab = ConnectionTab(make_session(FakeConn(), opener=FakeConn), "T", "PROD")
    tab.debugger.session_factory = FakeDebugSession
    with tab.db.lock:                                # la sesión principal está ocupada
        tab.start_debug("BEGIN NULL; END;")
        assert wait_until(app, lambda: "Esperando" in tab.debug_panel.output.toPlainText())
        tab.debug_panel.do("stop")
        assert wait_until(app, lambda: tab.debug_panel.location.text() == "Terminó con error", 3)
    assert wait_until(app, lambda: not tab.debugger.active)


def open_pkg_for_debug(app):
    tab = ConnectionTab(make_session(FakeConn(), opener=FakeConn), "T", "PROD")
    tab.debugger.session_factory = FakeDebugSession
    viewer = tab.open_object("SCOTT", "PKG", "PACKAGE")
    code = viewer.pages["Código"]
    assert wait_until(app, lambda: "PACKAGE BODY" in code.toPlainText())
    return tab, code


def test_debug_skips_the_hidden_test_block(app):
    tab, code = open_pkg_for_debug(app)
    # sin breakpoints: el primer paso cae en el bloque de prueba y sigue hasta entrar al paquete
    FakeDebugSession.script = [("anon", 2), 4, ("anon", 3), None]
    tab.start_debug("BEGIN SCOTT.PKG.ALTA; END;")
    panel = tab.debug_panel
    assert wait_until(app, lambda: "línea 4" in panel.location.text())
    assert code.exec_line == 9 and code.textCursor().selectedText() == "NULL;"
    panel.do("over")                               # vuelve al bloque de prueba: sigue solo hasta el final
    assert wait_until(app, lambda: panel.location.text() == "Terminado")
    steps = [c[1] for c in FakeDebugSession.last.calls if c[0] == "step"]
    assert steps == ["into", "into", "over", "into"]
    assert "No se detuvo" not in panel.output.toPlainText()


def test_debug_warns_when_it_never_stops_in_code(app):
    tab, _code = open_pkg_for_debug(app)
    FakeDebugSession.script = [("anon", 2), None]    # p. ej. el paquete no está compilado con DEBUG
    tab.start_debug("BEGIN SCOTT.PKG.ALTA; END;")
    assert wait_until(app, lambda: tab.debug_panel.location.text() == "Terminado")
    assert "No se detuvo dentro del código" in tab.debug_panel.output.toPlainText()


def test_hover_shows_variable_value_while_paused(app):
    editor = CodeEditor()
    editor.resize(600, 200)
    editor.setPlainText("BEGIN\n  v_total := v_total + 1;\nEND;")
    cur = editor.textCursor()
    cur.setPosition(len("BEGIN\n  v_to"))
    pos = editor.cursorRect(cur).center()
    assert editor.value_at(pos) is None                       # sin pausa no hay valores
    editor.set_debug_values([("V_TOTAL", "41")])
    assert editor.value_at(pos) == ("v_total", "41")
    cur.setPosition(len("BEGIN\n  v_total := v_total + 1;"))
    assert editor.value_at(editor.cursorRect(cur).center()) is None   # ';' no es una variable
    editor.set_debug_values([])
    assert editor.value_at(pos) is None


BLOCK = "BEGIN\n  SCOTT.PKG.ALTA;\nEND;"          # 3 líneas, como la plantilla que arma la app
TARGET = ("PACKAGE BODY", "SCOTT", "PKG")


def test_pause_without_unit_info_uses_the_debugged_object(app):
    """Lo que devolvió Oracle 19c: la línea 15 de la función, pero sin tipo ni nombre de programa."""
    tab, code = open_pkg_for_debug(app)
    code.toggle_breakpoint(9)
    FakeDebugSession.script = [("noname", 4), None]
    tab.start_debug(BLOCK, [], target=TARGET)
    panel = tab.debug_panel
    assert wait_until(app, lambda: "línea 4" in panel.location.text())
    assert "SCOTT.PKG (package body)" in panel.location.text()          # ya no "Bloque de prueba"
    assert code.exec_line == 9 and code.textCursor().selectedText() == "NULL;"
    assert panel.vars.model().rows == [("V_X", "42")]                    # ahora sí hay variables
    assert "Oracle no indicó la unidad" in panel.output.toPlainText()
    panel.do("continue")
    assert wait_until(app, lambda: panel.location.text() == "Terminado")


def test_pause_inside_test_block_lines_is_still_the_test_block(app):
    tab, _code = open_pkg_for_debug(app)
    FakeDebugSession.script = [("noname", 2), 4, None]   # línea 2: cabe en el bloque de prueba
    tab.start_debug(BLOCK, [], target=TARGET)
    assert wait_until(app, lambda: "línea 4" in tab.debug_panel.location.text())
    steps = [c[1] for c in FakeDebugSession.last.calls if c[0] == "step"]
    assert steps == ["into", "into"]                      # la línea 2 se saltó como bloque de prueba
    tab.debug_panel.do("continue")
    assert wait_until(app, lambda: tab.debug_panel.location.text() == "Terminado")


def test_pause_with_name_but_no_type_asks_all_objects(app):
    tab, code = open_pkg_for_debug(app)
    FakeDebugSession.script = [("notype", 4), ("lut", 5), None]
    tab.start_debug(BLOCK, [], target=TARGET)
    assert wait_until(app, lambda: code.exec_line == 9)
    assert ("unit_type", "SCOTT", "PKG") in FakeDebugSession.last.calls
    tab.debug_panel.do("over")                            # el siguiente trae solo el código 11
    assert wait_until(app, lambda: code.exec_line == 10)
    tab.debug_panel.do("continue")
    assert wait_until(app, lambda: tab.debug_panel.location.text() == "Terminado")


def test_marker_only_while_paused(app):
    tab, code = open_pkg_for_debug(app)
    code.toggle_breakpoint(9)
    FakeDebugSession.script = [4, 5, None]
    tab.start_debug(BLOCK, [], target=TARGET)
    panel = tab.debug_panel
    assert wait_until(app, lambda: code.exec_line == 9)
    assert code.textCursor().hasSelection() and code.debug_values and panel.vars.isEnabled()
    panel.do("over")
    # en cuanto sigue corriendo: sin marca, sin selección, sin valores al pasar el mouse, variables en gris
    assert code.exec_line is None and not code.textCursor().hasSelection()
    assert code.debug_values == {} and not panel.vars.isEnabled() and not panel.stack.isEnabled()
    assert wait_until(app, lambda: code.exec_line == 10)                 # la siguiente pausa vuelve a marcar
    assert code.textCursor().selectedText() == "END;" and panel.vars.isEnabled()
    panel.do("continue")
    assert code.exec_line is None
    assert wait_until(app, lambda: panel.location.text() == "Terminado")
    assert code.exec_line is None and not code.textCursor().hasSelection()


def test_ends_even_if_oracle_never_reports_the_end(app):
    """Al terminar el código, Oracle no avisa nada: la depuración igual se cierra sola."""
    tab, code = open_pkg_for_debug(app)
    code.toggle_breakpoint(9)
    FakeDebugSession.script = [4, "finish"]          # después de la pausa: termina sin aviso, solo timeouts
    tab.start_debug(BLOCK, [], target=TARGET)
    panel = tab.debug_panel
    assert wait_until(app, lambda: code.exec_line == 9)
    panel.do("continue")
    assert wait_until(app, lambda: panel.location.text() == "Terminado")
    assert not tab.debugger.active and not tab.db.busy                 # la sesión principal quedó libre
    assert ("detach",) in FakeDebugSession.last.calls
    assert ("timeout", 2) in FakeDebugSession.last.calls


def test_long_running_code_keeps_waiting_for_the_breakpoint(app):
    tab, code = open_pkg_for_debug(app)
    code.toggle_breakpoint(9)
    FakeDebugSession.script = ["timeout", "timeout", "timeout", 4, None]   # tarda en llegar al breakpoint
    tab.start_debug(BLOCK, [], target=TARGET)
    assert wait_until(app, lambda: code.exec_line == 9)
    assert FakeDebugSession.last.calls.count(("sync",)) == 3
    tab.debug_panel.do("continue")
    assert wait_until(app, lambda: tab.debug_panel.location.text() == "Terminado")


def test_debug_can_run_again_after_it_ends(app):
    tab, code = open_pkg_for_debug(app)
    code.toggle_breakpoint(9)
    panel = tab.debug_panel
    for run in range(3):
        FakeDebugSession.script = [4, "finish"] if run % 2 else [4, None]
        tab.start_debug(BLOCK, [], target=TARGET)
        assert wait_until(app, lambda: code.exec_line == 9), f"la corrida {run + 1} no se detuvo"
        assert panel.buttons["continue"].isEnabled()
        panel.do("continue")
        assert wait_until(app, lambda: panel.location.text() == "Terminado")
        assert not tab.debugger.active and not tab.db.busy
        assert ("bp", "PACKAGE BODY", "SCOTT", "PKG", 4) in FakeDebugSession.last.calls   # el breakpoint sigue
