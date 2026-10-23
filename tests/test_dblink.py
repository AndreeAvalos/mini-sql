"""Consultas por dblink con columnas BLOB/CLOB: la base remota no deja traerlas como bytes/texto (ORA-03149);
se reintenta pidiéndolas como referencia (LOB) y leyéndolas aparte."""
import oracledb
import pytest

from minisql.db.session import RemoteLobError, read_lobs, remote_lob_error

from support import FakeConn, FakeCursor, make_session


class FakeLob:
    def __init__(self, data, readable=True):
        self.data, self.readable = data, readable
        self.type = oracledb.DB_TYPE_BLOB

    def read(self):
        if not self.readable:
            raise RuntimeError("ORA-22992: cannot use LOB locators selected from remote tables")
        return self.data


class DblinkCursor(FakeCursor):
    """WMS_DOCUMENTOS@TF052 con una columna FIRMA (BLOB), como la tabla real."""
    attempts = []

    def __init__(self):
        super().__init__()
        self.outputtypehandler = None
        self.rowfactory = None

    def execute(self, sql, *args, **kwargs):
        super().execute(sql, *args, **kwargs)
        if "@tf052" not in self.sql:
            return
        DblinkCursor.attempts.append(self.outputtypehandler is not None)
        if "no_existe" in self.sql or self.outputtypehandler is None:
            raise RuntimeError("ORA-03149: invalid Oracle error code")         # lo que llega del dblink
        meta = type("Meta", (), {"type_code": oracledb.DB_TYPE_BLOB})()
        assert self.outputtypehandler(self, meta) is not None              # el BLOB se pide como referencia
        self.description = [("DOCUMENTO",), ("FIRMA",)]

    def var(self, *_a, **_k):
        return object()

    def fetchmany(self, n):
        if "@tf052" not in self.sql:
            return super().fetchmany(n)
        readable = "rota" not in self.sql
        rows = [(1, FakeLob(b"\x89PNG", readable)), (2, None)]
        return [self.rowfactory(*r) for r in rows]


class DblinkConn(FakeConn):
    def cursor(self):
        return DblinkCursor()


def test_blob_over_dblink_is_retried_as_a_locator():
    DblinkCursor.attempts = []
    res = make_session(DblinkConn()).execute("SELECT * FROM WMS_DOCUMENTOS@TF052 WHERE ROWNUM <= 10")
    assert DblinkCursor.attempts == [False, True]                          # falló como bytes, funcionó así
    assert res["headers"] == ["DOCUMENTO", "FIRMA"] and res["rows"] == [(1, b"\x89PNG"), (2, None)]


def test_unreadable_lob_shows_a_note_in_the_cell():
    res = make_session(DblinkConn()).execute("SELECT * FROM WMS_DOCUMENTOS@TF052 WHERE 'rota' = 'rota'")
    assert res["rows"][0] == (1, "<BLOB no se pudo leer por el dblink>")   # la fila se ve igual


def test_remote_errors_get_an_explanation():
    with pytest.raises(RemoteLobError) as err:
        make_session(DblinkConn()).execute("SELECT no_existe FROM WMS_DOCUMENTOS@TF052")
    text = str(err.value)
    assert "BLOB/CLOB" in text and "no existe" in text and "ORA-03149" in text


class LocatorFailsCursor(DblinkCursor):
    """La referencia remota tampoco se puede leer: el modo thin no entiende el locator que manda el dblink."""

    def fetchmany(self, n):
        if "@tf052" in self.sql:
            raise RuntimeError("DPY-5002: internal error: read integer of length 7 when expecting integer of "
                               "no more than length 4")
        return super().fetchmany(n)


class LocatorFailsConn(FakeConn):
    def cursor(self):
        return LocatorFailsCursor()


def test_unreadable_remote_locator_gets_an_explanation():
    with pytest.raises(RemoteLobError) as err:
        make_session(LocatorFailsConn()).execute("SELECT * FROM WMS_DOCUMENTOS@TF052")
    assert "BLOB/CLOB" in str(err.value) and "DPY-5002" in str(err.value)


def test_only_dblink_queries_are_retried():
    assert remote_lob_error(RuntimeError("ORA-03149: invalid Oracle error code"), "select * from t@link")
    assert not remote_lob_error(RuntimeError("ORA-03149"), "select '@' from t")              # @ en un texto
    assert not remote_lob_error(RuntimeError("ORA-00942: no existe"), "select * from t@link")
    assert read_lobs(1, "x", b"y", None) == (1, "x", b"y", None)
