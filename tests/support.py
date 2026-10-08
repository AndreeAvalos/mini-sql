"""Conexiones y cursores simulados, y utilidades para las pruebas (sin Oracle real)."""

import re
import threading
import time

from PySide6.QtWidgets import QDialog

from minisql.db.session import OracleSession
from minisql.ui.editors import SqlEditor


def make_session(conn=None, meta=None, opener=None):
    """Sesión simulada: principal, de metadatos y (opcional) cómo abrir otra para el depurador."""
    return OracleSession(conn or FakeConn(), meta or FakeConn(), opener)


def pump(app, n=20):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.02)


PKG_SOURCE = [
    ("PACKAGE", "PACKAGE PKG AS\n"), ("PACKAGE", "  PROCEDURE ALTA;\n"), ("PACKAGE", "END;\n"),
    ("PACKAGE BODY", "PACKAGE BODY PKG AS\n"), ("PACKAGE BODY", "  PROCEDURE ALTA IS\n"),
    ("PACKAGE BODY", "  BEGIN\n"), ("PACKAGE BODY", "    NULL;\n"), ("PACKAGE BODY", "  END;\n"),
    ("PACKAGE BODY", "END;\n"),
]


class FakeVar:
    def __init__(self, value=None):
        self.value = value

    def getvalue(self):
        return self.value

    def setvalue(self, _pos, value):
        self.value = value


class FakeCursor:
    executed = []                       # todas las sentencias (para revisar compilaciones)
    errors = []                         # lo que devuelve ALL_ERRORS para PKG

    buffer = []                         # simula el búfer de DBMS_OUTPUT de la sesión

    def var(self, *_a, **_k):
        return FakeVar()

    def arrayvar(self, *_a, **_k):
        return FakeVar([])

    def callproc(self, name, args):
        if name == "dbms_output.get_lines":
            lines_var, count_var = args
            n = count_var.value
            lines_var.value, FakeCursor.buffer[:] = FakeCursor.buffer[:n], FakeCursor.buffer[n:]
            count_var.value = len(lines_var.value)

    def __init__(self):
        self.description = None
        self.rowcount = 0
        self.arraysize = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def execute(self, sql, *args, **kwargs):
        self.sql, self.kw = sql.lower(), kwargs
        FakeCursor.executed.append(sql)
        if getattr(self, "debugging", False) and "dbms_debug" not in self.sql:
            # como en Oracle: el bloque depurado no termina hasta que el depurador lo deja terminar
            FakeDebugSession.finished.wait(10)
            self.debugging = False
        FakeCursor.buffer += re.findall(r"put_line\('([^']*)'\)", sql, re.I)
        if "raise_application_error" in self.sql:
            raise RuntimeError("ORA-20001: falló a propósito")
        if "dbms_debug.initialize" in self.sql:
            kwargs["sid"].value = "SID-1"
            self.debugging = True
        self.description = [("A",), ("B",)] if self.sql.lstrip().startswith("select") else None

    def fetchmany(self, n):
        return [(1, "x"), (2, None)]

    def fetchone(self):
        if "last_ddl_time" in self.sql:
            return ("TABLE", "VALID", None, None)
        if "all_synonyms" in self.sql:
            return ("SCOTT", "EMP")
        return ("\n  CREATE TABLE EMP (ID NUMBER)",)

    def fetchall(self):
        q = self.sql
        n = self.kw.get("n") or self.kw.get("nam")
        if q.lstrip().startswith("select object_type from all_objects"):
            kinds = {"EMP": "TABLE", "DEPT": "TABLE", "S_EMP": "SYNONYM", "PKG": "PACKAGE", "SEQ": "SEQUENCE"}
            return [(kinds[n],)] if n in kinds and self.kw["own"] == "SCOTT" else []
        if q.lstrip().startswith("select cc.column_name from all_cons_columns"):
            return [("ID",)]
        if "all_col_comments" in q:
            return [(1, "ID", "NUMBER", 22, 10, 0, 0, "N", None, "Clave"),
                    (2, "NOMBRE", "VARCHAR2", 50, None, None, 50, "Y", "'x' ", None)]
        if q.lstrip().startswith("select object_name, object_type from all_objects"):
            return [("EMP", "TABLE"), ("EMPLEO", "VIEW"), ("PKG", "PACKAGE"), ("SEQ", "SEQUENCE")]
        if "all_tab_columns" in q and "nullable" not in q:
            if n == "DEPT":
                return [("DEPTNO", "NUMBER", 22, 2, 0, 0)]
            return [("ID", "NUMBER", 22, 10, 0, 0), ("NOMBRE", "VARCHAR2", 50, None, None, 50)]
        if "from all_source" in q and n == "PKG":
            return PKG_SOURCE
        if "from all_errors" in q:
            return FakeCursor.errors if n == "PKG" else []
        if "all_users" in q:
            return [("HR",), ("SCOTT",)]
        if "all_tab_columns" in q:
            return [("ID", "NUMBER", 22, 10, 0, 0, "N", "P"), ("NOMBRE", "VARCHAR2", 50, None, None, 50, "Y", None)]
        if "all_procedures" in q:
            return [("ALTA",), ("BAJA",)]
        if "object_type = 'table'" in q:
            return [("EMP", "VALID"), ("DEPT", "INVALID")]
        if "all_objects" in q:
            return [("OBJ", "VALID")]
        return []


class FakeConn:
    username, dsn, version = "scott", "h:1521/X", "19.0"
    transaction_in_progress = False

    def cursor(self):
        return FakeCursor()

    def commit(self): pass
    def rollback(self): pass
    def cancel(self): pass
    def close(self): pass


def suggestions(app, sheet, text, manual=True):
    sheet.editor.setPlainText(text)
    cur = sheet.editor.textCursor()
    cur.setPosition(len(text))
    sheet.editor.setTextCursor(cur)
    for _ in range(3):               # la primera vez pide metadatos en un hilo
        sheet.editor.show_completions(manual)
        pump(app, 10)
    model = sheet.editor.cmodel
    return [model.item(i).data(SqlEditor.NAME_ROLE) for i in range(model.rowCount())]


def wait_until(app, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


PKG_CODE = (
    "CREATE OR REPLACE PACKAGE PKG AS\n  PROCEDURE ALTA;\nEND;\n/\n\n"
    "CREATE OR REPLACE PACKAGE BODY PKG AS\n  PROCEDURE ALTA IS\n  BEGIN\n    NULL;\n  END;\nEND;\n/"
)


class FakeDebugSession:
    script = []
    last = None

    finished = threading.Event()

    def __init__(self, conn):
        self.calls = []
        self.synced = False
        FakeDebugSession.last = self
        FakeDebugSession.finished = threading.Event()

    def compile_debug(self, *a): self.calls.append(("compile", *a))
    def attach(self, sid): self.calls.append(("attach", sid))
    def set_timeout(self, seconds): self.calls.append(("timeout", seconds))

    def synchronize(self):
        if not self.synced:                       # al adjuntarse: detenido al inicio del bloque de prueba
            self.synced = True
            return {"line": 1, "owner": None, "name": None, "utype": None, "done": 0}
        self.calls.append(("sync",))
        return self._next()
    def set_breakpoint(self, *key): self.calls.append(("bp", *key)); return 7
    def delete_breakpoint(self, bp): self.calls.append(("del", bp))
    def variables(self, names): return [("V_X", "42")]
    def backtrace(self): return "PKG.ALTA línea 4"
    def source(self, *a): return "v_x := 42;"
    def detach(self): self.calls.append(("detach",))
    def unit_type(self, owner, name): self.calls.append(("unit_type", owner, name)); return "PACKAGE BODY"
    def close(self): pass

    def step(self, cmd):
        self.calls.append(("step", cmd))
        if cmd == "abort":
            FakeDebugSession.finished.set()
            return {"line": 0, "owner": None, "name": None, "utype": None, "done": 1}
        return self._next()

    def _next(self):
        """Siguiente evento del guion. "timeout": Oracle no avisó nada; "finish": el programa terminó en la
        sesión principal pero Oracle no avisa (luego solo hay timeouts); None: Oracle avisa que terminó."""
        line = self.script.pop(0) if self.script else "timeout"
        if line in ("timeout", "finish"):
            if line == "finish":
                FakeDebugSession.finished.set()
            return {"timeout": True}
        if line is None:
            FakeDebugSession.finished.set()
            return {"line": 0, "owner": None, "name": None, "utype": None, "done": 1}
        if isinstance(line, tuple):
            kind, n = line
            if kind == "anon":                   # detenido en el bloque de prueba
                return {"line": n, "owner": None, "name": None, "utype": None, "done": 0}
            if kind == "noname":                 # Oracle no informa la unidad (visto en 19c)
                return {"line": n, "owner": None, "name": None, "utype": None, "lut": None, "done": 0}
            if kind == "notype":                 # informa el nombre pero no el tipo
                return {"line": n, "owner": "SCOTT", "name": "PKG", "utype": None, "lut": -1, "done": 0}
            if kind == "lut":                    # solo el código numérico (11 = cuerpo de paquete)
                return {"line": n, "owner": "SCOTT", "name": "PKG", "utype": None, "lut": 11, "done": 0}
        return {"line": line, "owner": "SCOTT", "name": "PKG", "utype": "PACKAGE BODY", "done": 0}


class FakeDialog:
    accept = True

    def __init__(self, parent=None, preset=None):
        preset = preset or {"name": "PROD", "dsn": "PROD"}     # el usuario elige el perfil PROD
        self.conn_name, self.dsn_label = preset["name"], preset["dsn"]
        self.session = make_session()

    def exec(self):
        return QDialog.Accepted if FakeDialog.accept else QDialog.Rejected


def run_and_wait(app, sheet, sql):
    sheet.editor.setPlainText(sql)
    sheet.run()
    assert wait_until(app, lambda: not sheet.running)


class SlowConn(FakeConn):
    """Una consulta con 'lenta' se queda corriendo hasta que la cancelan (si responde a cancel)."""

    def __init__(self, responds=True):
        self.release = threading.Event()
        self.responds = responds
        self.cancelled = False

    def cursor(self):
        conn = self

        class SlowCursor(FakeCursor):
            def execute(self, sql, *a, **k):
                if "lenta" in sql:
                    conn.release.wait(10)
                    raise RuntimeError("ORA-01013: el usuario ha solicitado la cancelación")
                return super().execute(sql, *a, **k)
        return SlowCursor()

    def cancel(self):
        self.cancelled = True
        if self.responds:
            self.release.set()


def start_slow_query(app, w, conn):
    tab = w.add_connection(make_session(conn), "DEV", "DEV")
    sheet = tab.current_sheet()
    sheet.editor.setPlainText("select * from tabla_lenta")
    sheet.run()
    assert wait_until(app, lambda: tab.db.busy)
    return tab, sheet
