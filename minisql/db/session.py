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

from ..config import MAX_OUTPUT_LINES, MAX_ROWS
from ..sql.text import DDL, DML, first_keyword


class SessionBusy(Exception):
    """La sesión principal está ejecutando otra cosa."""


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
        """Usuario tal como se conectó."""
        return self.conn.username or ""

    @property
    def schema(self):
        """Esquema del usuario (en mayúsculas, como lo guarda Oracle)."""
        return self.user.upper()

    @property
    def busy(self):
        return self.lock.locked()

    @property
    def transaction_in_progress(self):
        return getattr(self.conn, "transaction_in_progress", None)

    # --- sesión principal
    def execute(self, sql, on_output=None):
        """Ejecuta una sentencia en la sesión principal y devuelve un dict con el resultado:
        headers/rows/truncated si es una consulta, o message. Llama on_output(líneas) con lo que
        imprimió DBMS_OUTPUT, también si la sentencia falla (el error se propaga después)."""
        kw = first_keyword(sql)
        t0 = time.perf_counter()
        with self.lock:
            with self.conn.cursor() as cur:
                self._enable_output(cur)
                try:
                    res = self._run(cur, sql, kw)
                finally:
                    self._deliver_output(cur, on_output)
            res["txn"] = self.transaction_in_progress
        res["kw"] = kw
        res["elapsed"] = time.perf_counter() - t0
        return res

    @staticmethod
    def _run(cur, sql, kw):
        cur.arraysize = 500
        cur.execute(sql)
        if cur.description:
            rows = cur.fetchmany(MAX_ROWS + 1)
            return {
                "headers": [d[0] for d in cur.description],
                "rows": rows[:MAX_ROWS],
                "truncated": len(rows) > MAX_ROWS,
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

    def _deliver_output(self, cur, on_output):
        if not self.output_enabled:
            return
        try:
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
        with self.meta_lock, self.meta_conn.cursor() as cur:
            return fn(cur)

    def close(self):
        for c in ([self.conn, self.meta_conn] if self.own_meta else [self.conn]):
            with contextlib.suppress(Exception):
                c.close()
