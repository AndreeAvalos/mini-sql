"""Consultas por dblink con columnas BLOB/CLOB: la base remota no deja traerlas (ORA-03149); se repite la consulta
con cada LOB cambiado por un aviso, así se ven las demás columnas. Nunca se piden como referencia (locator): el modo
thin no entiende el locator remoto (DPY-5002) y ese error deja la conexión inservible."""
import oracledb
import pytest

from minisql.db.session import RemoteLobError, remote_lob_error

from support import FakeConn, FakeCursor, make_session

NOTE = "<BLOB: no se puede leer por el dblink>"


class DblinkCursor(FakeCursor):
    """WMS_DOCUMENTOS@MX047 con una columna FIRMA (BLOB), como la tabla real."""
    executed = []
    parsed = []

    def __init__(self):
        super().__init__()
        self.outputtypehandler = None

    def execute(self, sql, *args, **kwargs):
        super().execute(sql, *args, **kwargs)
        DblinkCursor.executed.append(sql)
        if "@mx047" not in self.sql:
            return
        assert self.outputtypehandler is None                              # nunca como referencia (locator)
        if "where 1 = 0" in self.sql:                                       # ejecutarla define el LOB: falla
            raise RuntimeError("ORA-03149: invalid Oracle error code")
        if "no se puede leer" in self.sql:                                  # la consulta sin el LOB
            self.description = [("NO_DOCUMENTO",), ("FIRMA",)]
        else:
            raise RuntimeError("ORA-03149: invalid Oracle error code")     # lo que llega del dblink

    def parse(self, sql):
        """Oracle solo describe la consulta (no la ejecuta): sin defines, sin LOB que crucen el dblink."""
        DblinkCursor.parsed.append(sql)
        low = sql.lower()
        if "no_existe" in low:
            raise RuntimeError("ORA-03149: invalid Oracle error code")
        if "sin_lob" in low:
            self.description = [("NO_DOCUMENTO", oracledb.DB_TYPE_VARCHAR)]
            return
        names = ("ID", "ID") if "repetida" in low else ("NO_DOCUMENTO", "FIRMA")
        # como python-oracledb con fetch_lobs=False: el BLOB se describe como LONG RAW
        self.description = [(names[0], oracledb.DB_TYPE_NUMBER), (names[1], oracledb.DB_TYPE_LONG_RAW)]

    def fetchmany(self, n):
        if "no se puede leer" in self.sql:
            return [(131083, NOTE)]
        return super().fetchmany(n)


class DblinkConn(FakeConn):
    def cursor(self):
        return DblinkCursor()


QUERY = "SELECT * FROM WMS_DOCUMENTOS@MX047\nWHERE 1 = 1\n--AND serie = 'MX047-2'\nAND NO_DOCUMENTO = 131083"


def test_blob_over_dblink_shows_the_other_columns():
    DblinkCursor.executed, DblinkCursor.parsed = [], []
    res = make_session(DblinkConn()).execute(QUERY)
    assert DblinkCursor.parsed == [QUERY]                               # las columnas: solo descritas
    # la consulta con el LOB nunca se ejecuta: ese ORA-03149, repetido, termina tirando la sesión
    remote = [s for s in DblinkCursor.executed if "@MX047" in s]
    assert len(remote) == 1 and "no se puede leer" in remote[0]
    assert res["headers"] == ["NO_DOCUMENTO", "FIRMA"] and res["rows"] == [(131083, NOTE)]
    plain = DblinkCursor.executed[-1]
    assert plain.startswith(f'select "NO_DOCUMENTO", \'{NOTE}\' as "FIRMA"\nfrom (\n')
    assert "--AND serie = 'MX047-2'\n" in plain                         # el comentario no se come el resto


def test_remote_errors_get_an_explanation():
    with pytest.raises(RemoteLobError) as err:                          # tampoco se pudo describir
        make_session(DblinkConn()).execute("SELECT no_existe FROM WMS_DOCUMENTOS@MX047")
    text = str(err.value)
    assert "BLOB/CLOB" in text and "no existe" in text and "ORA-03149" in text


def test_repeated_column_names_cannot_be_rewritten():
    with pytest.raises(RemoteLobError) as err:
        make_session(DblinkConn()).execute("SELECT a.id, b.id FROM WMS_DOCUMENTOS@MX047 a -- repetida")
    assert "mismo nombre" in str(err.value)


def test_without_lobs_the_error_is_from_the_remote_database():
    with pytest.raises(RemoteLobError) as err:                          # ORA-03149 que no es por un LOB
        make_session(DblinkConn()).execute("SELECT no_documento FROM sin_lob@MX047 WHERE x = 1")
    assert "no trae columnas BLOB/CLOB" in str(err.value) and "tipo de dato" in str(err.value)


def test_queries_without_dblink_are_not_described_first():
    DblinkCursor.parsed = []
    make_session(DblinkConn()).execute("select a, b from t where x = '@'")
    assert DblinkCursor.parsed == []


def test_only_dblink_queries_are_retried():
    assert remote_lob_error(RuntimeError("ORA-03149: invalid Oracle error code"), "select * from t@link")
    assert not remote_lob_error(RuntimeError("ORA-03149"), "select '@' from t")              # @ en un texto
    assert not remote_lob_error(RuntimeError("ORA-00942: no existe"), "select * from t@link")
