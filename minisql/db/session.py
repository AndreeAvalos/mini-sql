"""Sesión de Oracle de una pestaña de conexión, sin nada de interfaz.

Cada conexión de la app usa dos sesiones de Oracle:
- la principal, donde corren las hojas y vive la transacción del usuario (autocommit apagado);
- otra para metadatos (explorador, autocompletado, compilación), para que no se bloqueen
  mientras una hoja ejecuta algo largo.

Cada sesión tiene su candado: Oracle no admite dos llamadas a la vez en la misma conexión.
Los métodos que hablan con Oracle se llaman desde hilos de trabajo, nunca desde la interfaz.
"""
import contextlib
import threading
import time

import oracledb

from ..config import MAX_OUTPUT_LINES, PAGE_ROWS
from ..sql.editable import rowid_query
from ..sql.text import DDL, DML, blank_literals, first_keyword
from .grid import apply_changes


class SessionBusy(Exception):
    """La sesión principal está ejecutando otra cosa."""


class NotConnected(Exception):
    """La conexión está cerrada (se desconectó o se perdió): hay que reconectar."""


# Errores que dicen que la sesión de Oracle ya no existe (red caída, sesión terminada, timeout por inactividad…)
LOST_CONNECTION = ("DPY-1001", "DPY-4011", "DPI-1080", "ORA-00028", "ORA-01012", "ORA-02396", "ORA-03113",
                   "ORA-03114", "ORA-03135", "ORA-12537")


def connection_lost(error):
    """True si el error significa que se perdió la conexión con Oracle."""
    return isinstance(error, NotConnected) or any(code in str(error) for code in LOST_CONNECTION)


class RemoteLobError(Exception):
    """Una columna LOB de una tabla remota no se pudo traer por el dblink."""


# Errores de una columna LOB (BLOB/CLOB) traída por un dblink: ORA-22992 es el clásico; con el modo thin y los LOB
# pedidos como bytes/texto, la base remota responde ORA-03149 ("código de error no válido").
REMOTE_LOB_CODES = ("ORA-03149", "ORA-22992")
REMOTE_LOB_HELP = ("La base remota rechazó la consulta y su mensaje no llegó por el dblink. Suele ser:\n"
                   "• una columna BLOB/CLOB que el dblink no deja leer: quítala del SELECT (en vez de *, lista las "
                   "columnas) o trae solo una parte con DBMS_LOB.GETLENGTH(col) o DBMS_LOB.SUBSTR(col, 2000, 1);\n"
                   "• una columna o tabla que no existe con ese nombre en la base remota, o falta de permisos.")


def remote_lob_error(error, sql):
    """True si el error es de un LOB remoto: uno de esos códigos y la consulta usa un dblink (tabla@link)."""
    return any(code in str(error) for code in REMOTE_LOB_CODES) and "@" in blank_literals(sql)


def lob_locators(cursor, metadata):
    """outputtypehandler: los LOB llegan como referencia (LOB), que sí cruzan el dblink en Oracle 12.2+."""
    if metadata.type_code in (oracledb.DB_TYPE_BLOB, oracledb.DB_TYPE_CLOB, oracledb.DB_TYPE_NCLOB):
        return cursor.var(metadata.type_code, arraysize=cursor.arraysize)
    return None


def read_lobs(*row):
    """rowfactory: cada referencia LOB se lee y queda como bytes/texto, igual que el resto de los resultados.
    Si el dblink tampoco deja leerla, la celda lo dice y el resto de la fila se ve igual."""
    return tuple(_read_lob(v) for v in row)


def _read_lob(value):
    if isinstance(value, (str, bytes)) or not hasattr(value, "read"):
        return value
    try:
        return value.read()
    except Exception:
        kind = getattr(getattr(value, "type", None), "name", "LOB").replace("DB_TYPE_", "")
        return f"<{kind} no se pudo leer por el dblink>"


class ScriptError(Exception):
    """Falló una sentencia de un script; el mensaje dice cuál y qué pasó con las anteriores."""


class RowStream:
    """Cursor abierto de una consulta, para traer más filas cuando se piden (paginación de resultados).
    Cada pedido toma el candado de la sesión principal: Oracle permite tener el cursor abierto mientras
    se ejecutan otras sentencias, pero no dos llamadas a la vez. Se cierra solo al llegar al final."""

    def __init__(self, session, cursor):
        self.session = session
        self.cursor = cursor
        self.with_rowid = False        # la primera columna es el ROWID (grilla editable)
        self.done = False

    def fetch(self, n):
        """Hasta n filas más: (filas, rowids o None)."""
        if self.done:
            return [], ([] if self.with_rowid else None)
        with self.session.lock:
            rows = self.cursor.fetchmany(n)
            if len(rows) < n:
                self._close_locked()
        if not self.with_rowid:
            return rows, None
        return [r[1:] for r in rows], [r[0] for r in rows]

    def fetch_all(self, limit):
        """Lo que queda, hasta limit filas."""
        rows, rowids = [], [] if self.with_rowid else None
        while not self.done and len(rows) < limit:
            more, ids = self.fetch(min(PAGE_ROWS * 10, limit - len(rows)))
            rows += more
            if rowids is not None:
                rowids += ids
        return rows, rowids

    def close(self):
        if not self.done:
            with self.session.lock:
                self._close_locked()

    def _close_locked(self):
        self.done = True
        with contextlib.suppress(Exception):
            self.cursor.close()


def read_dbms_output(cur, limit=MAX_OUTPUT_LINES, chunk=100):
    """Lee lo que dejó DBMS_OUTPUT.PUT_LINE en la sesión, de 100 en 100 líneas (un viaje a Oracle
    por bloque, no por línea)."""
    lines_var = cur.arrayvar(str, chunk)
    count_var = cur.var(int)
    lines = []
    while len(lines) < limit:
        count_var.setvalue(0, chunk)
        cur.callproc("dbms_output.get_lines", (lines_var, count_var))
        n = count_var.getvalue() or 0
        lines += [line or "" for line in lines_var.getvalue()[:n]]
        if n < chunk:
            break
    return lines[:limit]


class OracleSession:
    def __init__(self, conn, meta_conn=None, opener=None):
        """conn: sesión principal. meta_conn: sesión de metadatos (si es None se comparte la principal).
        opener: función que abre otra sesión con las mismas credenciales (para el depurador)."""
        self.conn = conn
        self.own_meta = meta_conn is not None
        self.meta_conn = meta_conn or conn
        self.lock = threading.Lock()
        self.meta_lock = threading.Lock() if self.own_meta else self.lock
        self.opener = opener
        self.output_enabled = False       # DBMS_OUTPUT.ENABLE ya se llamó en la sesión principal
        self.connected = True             # False: desconectada a pedido o conexión perdida
        self._user = getattr(conn, "username", "") or ""

    @classmethod
    def open(cls, user, password, dsn):
        """Conecta la sesión principal y, si se puede, la de metadatos. La contraseña solo queda
        en memoria (dentro de opener), nunca en archivos."""
        params = dict(user=user, password=password, dsn=dsn)
        conn = oracledb.connect(**params)
        conn.autocommit = False
        try:
            meta = oracledb.connect(**params)
        except oracledb.Error:
            meta = None                   # se comparte la principal
        return cls(conn, meta, lambda: oracledb.connect(**params))

    # --- datos de la sesión
    @property
    def user(self):
        """Usuario tal como se conectó (se recuerda: con la conexión cerrada no se puede preguntar)."""
        return self._user

    @property
    def schema(self):
        """Esquema del usuario (en mayúsculas, como lo guarda Oracle)."""
        return self.user.upper()

    @property
    def busy(self):
        return self.lock.locked()

    @property
    def transaction_in_progress(self):
        if not self.connected:
            return None
        try:
            return getattr(self.conn, "transaction_in_progress", None)
        except Exception:
            return None

    # --- desconectar y reconectar
    def disconnect(self):
        """Cierra las sesiones de Oracle (Oracle deshace lo que no se confirmó). Se puede reconectar."""
        self.connected = False
        self.output_enabled = False
        self.close()

    def reconnect(self):
        """Abre sesiones nuevas con las mismas credenciales (las de opener, solo en memoria) y cierra las
        anteriores si quedaban. Corre en un hilo: es una llamada de red."""
        if self.opener is None:
            raise NotConnected("No hay datos para reconectar: cierra la conexión y vuelve a abrirla.")
        if not self.lock.acquire(blocking=False):
            raise SessionBusy("Hay una consulta en ejecución en esta conexión; espera a que termine.")
        try:
            conn = self.opener()
            conn.autocommit = False
            try:
                meta = self.opener()
            except oracledb.Error:
                meta = None                       # se comparte la principal
            self.close()                          # las viejas, si seguían abiertas
            self.conn, self.meta_conn = conn, meta or conn
            if (meta is not None) != self.own_meta:
                self.meta_lock = threading.Lock() if meta is not None else self.lock
            self.own_meta = meta is not None
            self._user = getattr(conn, "username", "") or self._user
            self.output_enabled = False
            self.connected = True
        finally:
            self.lock.release()

    def _require_connection(self):
        if not self.connected:
            raise NotConnected("Sin conexión: usa ⟳ Reconectar.")

    # --- sesión principal
    def execute(self, sql, on_output=None):
        """Ejecuta una sentencia en la sesión principal y devuelve un dict con el resultado:
        headers/rows/more si es una consulta (la primera página), o message. Si quedan filas, res["stream"]
        es un RowStream para traer las siguientes páginas. Llama on_output(líneas) con lo que imprimió
        DBMS_OUTPUT, también si la sentencia falla (el error se propaga después)."""
        self._require_connection()
        kw = first_keyword(sql)
        t0 = time.perf_counter()
        with self.lock:
            cur, keep_open = self.conn.cursor(), False
            try:
                self._enable_output(cur)
                try:
                    res = self._run(cur, sql, kw)
                finally:
                    self._deliver_output(on_output)
                if res.get("more"):
                    res["stream"] = RowStream(self, cur)      # el cursor queda abierto para la página siguiente
                    keep_open = True
            finally:
                if not keep_open:
                    cur.close()
            res["txn"] = self.transaction_in_progress
        res["kw"] = kw
        res["elapsed"] = time.perf_counter() - t0
        return res

    def execute_with_rowid(self, sql, on_output=None):
        """Como execute, pero trae también el ROWID de cada fila (para editar los resultados).
        Si Oracle no lo permite (p. ej. ORA-01446 en una consulta agregada), ejecuta la consulta tal cual
        y deja en res["not_editable"] el motivo; los resultados se muestran igual, en solo lectura."""
        try:
            res = self.execute(rowid_query(sql), on_output)
        except oracledb.DatabaseError as e:
            res = self.execute(sql, on_output)
            res["not_editable"] = str(e).splitlines()[0]
            return res
        res["rowids"] = [row[0] for row in res["rows"]]
        res["rows"] = [row[1:] for row in res["rows"]]
        res["headers"] = res["headers"][1:]
        if res.get("stream"):
            res["stream"].with_rowid = True
        return res

    def execute_script(self, statements, on_output=None):
        """Ejecuta varias sentencias en orden en la sesión principal (como un script de SQL*Plus).
        Se detiene en la primera que falla (ScriptError); las anteriores quedan hechas y, si cambiaron
        datos, pendientes de Commit. Devuelve el resultado de la última consulta que trajo filas, o un resumen."""
        self._require_connection()
        t0 = time.perf_counter()
        total, kws, grid, changed = len(statements), [], None, 0
        with self.lock:
            with self.conn.cursor() as cur:
                self._enable_output(cur)
                try:
                    for n, sql in enumerate(statements, 1):
                        kw = first_keyword(sql)
                        try:
                            res = self._run(cur, sql, kw)      # de una consulta: solo la primera página
                        except Exception as e:
                            raise ScriptError(self._script_failure(n, total, sql, e)) from e
                        kws.append(kw)
                        if "headers" in res:
                            grid = res
                        elif kw in DML:
                            changed += max(cur.rowcount or 0, 0)
                finally:
                    self._deliver_output(on_output)
            txn = self.transaction_in_progress
        message = f"{total} sentencias ejecutadas"
        if changed:
            message += f" · {changed} filas afectadas"
        if txn:
            message += " (pendiente de Commit)"
        res = dict(grid or {})
        res.update(message=message, script=total, txn=txn, elapsed=time.perf_counter() - t0,
                   kw=next((k for k in kws if k in DDL), kws[-1]))
        return res

    def _script_failure(self, n, total, sql, error):
        preview = " ".join(sql.split())
        preview = preview if len(preview) <= 70 else preview[:70] + "…"
        done = ""
        if n > 1:
            done = f"\nLas {n - 1} anteriores sí se ejecutaron"
            done += " y sus cambios quedan pendientes de Commit." if self.transaction_in_progress else "."
        return f"Sentencia {n} de {total}: {preview}\n{error}{done}"

    def apply_grid_changes(self, owner, table, ops):
        """Guarda los cambios de la grilla en la sesión principal (quedan pendientes de Commit)."""
        self._require_connection()
        if not self.lock.acquire(blocking=False):
            raise SessionBusy("Hay una consulta en ejecución en esta conexión; espera a que termine.")
        try:
            with self.conn.cursor() as cur:
                rowids = apply_changes(cur, owner, table, ops)
            return {"rowids": rowids, "txn": self.transaction_in_progress}
        finally:
            self.lock.release()

    @staticmethod
    def _run(cur, sql, kw):
        cur.arraysize = PAGE_ROWS
        try:
            cur.execute(sql)
            rows = cur.fetchmany(PAGE_ROWS) if cur.description else None
        except Exception as e:
            if not remote_lob_error(e, sql):
                raise
            # Un BLOB/CLOB por un dblink: la base remota no deja convertirlo a bytes/texto (fetch_lobs=False)
            # y responde ORA-03149 o ORA-22992. Se repite pidiendo el LOB como referencia y leyéndolo aparte.
            cur.outputtypehandler = lob_locators
            cur.rowfactory = read_lobs
            try:
                cur.execute(sql)
                rows = cur.fetchmany(PAGE_ROWS) if cur.description else None
            except Exception as again:
                if remote_lob_error(again, sql):
                    raise RemoteLobError(REMOTE_LOB_HELP + f"\n\n{again}") from again
                raise
        if cur.description:
            return {
                "headers": [d[0] for d in cur.description],
                "rows": rows,
                "more": len(rows) == PAGE_ROWS,       # puede haber más (se sabe al pedir la página siguiente)
            }
        if kw in DML:
            return {"message": f"{cur.rowcount} filas afectadas (pendiente de Commit)"}
        if kw in DDL:
            return {"message": "Sentencia DDL ejecutada"}
        return {"message": "Ejecutado correctamente"}

    def _enable_output(self, cur):
        if not self.output_enabled:
            try:
                cur.execute("begin dbms_output.enable(null); end;")   # sin límite de tamaño
                self.output_enabled = True
            except oracledb.Error:
                pass

    def _deliver_output(self, on_output):
        """Lee DBMS_OUTPUT con otro cursor: el de la consulta puede quedar abierto para paginar."""
        if not self.output_enabled:
            return
        try:
            with self.conn.cursor() as cur:
                lines = read_dbms_output(cur)
        except Exception:
            return
        if lines and on_output:
            on_output(lines)

    def commit(self):
        self._with_free_connection(self.conn.commit)

    def rollback(self):
        self._with_free_connection(self.conn.rollback)

    def _with_free_connection(self, action):
        self._require_connection()
        if not self.lock.acquire(blocking=False):
            raise SessionBusy("Hay una consulta en ejecución en esta conexión.")
        try:
            action()
        finally:
            self.lock.release()

    def cancel(self):
        """Interrumpe la llamada en curso de la sesión principal (Oracle responde con ORA-01013)."""
        with contextlib.suppress(Exception):
            self.conn.cancel()

    def wait_free(self, timeout):
        """True si la sesión principal queda libre antes de timeout segundos."""
        if self.lock.acquire(timeout=timeout):
            self.lock.release()
            return True
        return False

    # --- sesión de metadatos
    def with_meta_cursor(self, fn):
        """Ejecuta fn(cursor) en la sesión de metadatos y devuelve su resultado."""
        self._require_connection()
        with self.meta_lock, self.meta_conn.cursor() as cur:
            return fn(cur)

    def close(self):
        for c in ([self.conn, self.meta_conn] if self.own_meta else [self.conn]):
            with contextlib.suppress(Exception):
                c.close()
