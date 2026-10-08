"""Coordinación de la depuración PL/SQL entre la sesión principal y la depuradora."""

import contextlib
import queue
import threading
import time
from typing import ClassVar

import oracledb
from PySide6.QtCore import QObject, Signal

from ..db.debug import DebugSession
from ..db.session import read_dbms_output
from ..sql.plsql import backtrace_line, debug_wrapper, same_code, variable_candidates

# Códigos de DBMS_DEBUG.LIBUNITTYPE_* (por si la traducción en PL/SQL no llega)
LIBUNIT_TYPES = {7: "PROCEDURE", 8: "FUNCTION", 9: "PACKAGE", 11: "PACKAGE BODY", 12: "TRIGGER"}


class PlsqlDebugger(QObject):
    """Depuración con DBMS_DEBUG. La sesión principal es la que ejecuta el código (así sus cambios
    quedan pendientes de Commit como siempre) y una sesión nueva lo controla. Todo lo que habla con
    Oracle corre en hilos; la interfaz manda órdenes por una cola y recibe señales."""
    stopped = Signal(dict)
    resumed = Signal()                # el código vuelve a correr: lo mostrado de la pausa ya no vale
    message = Signal(str)
    ended = Signal(dict)

    def __init__(self, db, parent=None, session_factory=DebugSession):
        """db: OracleSession de la conexión (su sesión principal ejecuta el código)."""
        super().__init__(parent)
        self.db = db
        self.session_factory = session_factory
        self.active = False
        self.running = False          # el código está corriendo (el depurador espera)
        self.commands = None
        self.bps = {}                 # (tipo, dueño, nombre, línea) -> número de breakpoint

    def start(self, block, breakpoints, compile_targets=(), target=None):
        """target: (tipo de unidad, dueño, nombre) del código que se quiere depurar; sirve para ubicar
        la pausa cuando Oracle no informa en qué unidad se detuvo."""
        if self.active:
            return
        self.active = self.running = True
        self.target = target
        block = debug_wrapper(block)
        self.block_text = block.split("\n")
        self.block_lines = len(self.block_text)
        self.target_source = None
        self.warned = False
        self.commands = queue.Queue()
        self.bps = {bp: None for bp in breakpoints}
        state = {"sid": queue.Queue(), "ready": threading.Event(), "abort": False, "started": False,
                 "block_done": threading.Event(),         # el bloque de prueba ya terminó en la sesión principal
                 "detached": threading.Event(), "target_done": threading.Event(), "result": {}}
        self.state = state
        threading.Thread(target=self._target, args=(block, state), daemon=True).start()
        threading.Thread(target=self._debugger, args=(state, list(compile_targets)), daemon=True).start()

    # --- órdenes desde la interfaz
    def command(self, cmd):
        if self.active and not self.running:
            self.running = True
            self.commands.put(cmd)
            self.resumed.emit()

    def stop(self):
        if not self.active:
            return
        st = self.state
        if not st["started"]:                     # todavía preparando: que no llegue a ejecutar
            st["abort"] = True
            st["sid"].put(("error", "Depuración detenida"))
        elif self.running:
            self.db.cancel()                      # el código corre sin pausa: cancelar la llamada
        self.running = True
        self.commands.put("abort")
        self.resumed.emit()

    def breakpoint_changed(self, key, added):
        if self.active:
            self.commands.put(("bp", key, added))
        elif added:
            self.bps[key] = None
        else:
            self.bps.pop(key, None)

    # --- sesión que ejecuta (la principal)
    def _target(self, block, st):
        st["ready"].wait()
        if st["abort"]:
            return
        res = st["result"]
        try:
            with self.db.lock:
                if st["abort"]:                   # el depurador falló mientras esperábamos la sesión
                    return
                st["started"] = True
                with self.db.conn.cursor() as cur:
                    sid = cur.var(str, 200)
                    try:
                        cur.execute("""
                            begin
                              :sid := dbms_debug.initialize();
                              dbms_debug.debug_on();
                              dbms_output.enable(null);
                            end;""", sid=sid)
                    except Exception as e:
                        st["sid"].put(("error", str(e)))
                        raise
                    st["sid"].put(("ok", sid.getvalue()))
                    t0 = time.perf_counter()
                    try:
                        cur.execute(block)
                        res["message"] = "Terminó correctamente"
                    except oracledb.Error as e:
                        res["error"] = str(e)
                    res["elapsed"] = time.perf_counter() - t0
                    st["block_done"].set()           # el bloque ya hizo DEBUG_OFF (ver debug_wrapper)
                    with contextlib.suppress(Exception):
                        res["output"] = read_dbms_output(cur)
                    self.db.output_enabled = True   # el depurador activó DBMS_OUTPUT en la sesión
                    res["txn"] = self.db.transaction_in_progress
        except Exception as e:
            res.setdefault("error", str(e))
        finally:
            st["target_done"].set()

    MAX_HIDDEN_STEPS = 200
    WAIT_S = 2                        # espera máxima por evento antes de revisar si el código terminó
    TARGET_WAIT_S = 15                # al terminar: cuánto esperar a la sesión principal antes de cancelarla
    FINISHED: ClassVar[dict] = {"line": 0, "owner": None, "name": None, "utype": None, "done": 1}

    def _wait_event(self, ds, info):
        """Si Oracle no avisó nada en WAIT_S segundos: o el código ya terminó (la sesión principal salió
        del bloque) y la depuración se cierra, o sigue corriendo y se vuelve a esperar."""
        while info.get("timeout"):
            if self.state["block_done"].is_set():
                return dict(self.FINISHED)
            info = ds.synchronize()
        return info

    def _step(self, ds, cmd):
        """Un paso del depurador. El bloque de prueba no se muestra en ningún editor, así que si el
        paso queda ahí, sigue entrando hasta llegar a código con nombre (o al final)."""
        info = self._locate(ds, self._wait_event(ds, ds.step(cmd)))
        if cmd in ("continue", "abort"):
            return info
        for _ in range(self.MAX_HIDDEN_STEPS):
            if info["done"] or info["utype"] or self.state["block_done"].is_set():
                break
            info = self._locate(ds, self._wait_event(ds, ds.step("into")))
        return info

    def _locate(self, ds, info):
        """Completa en qué unidad está la pausa. Oracle a veces no llena el tipo (o ni el nombre) del
        programa; entonces se deduce: por el código numérico, por ALL_OBJECTS o, si la línea no
        puede ser del bloque de prueba, por el objeto que se está depurando."""
        if info["done"] or info["utype"]:
            return info
        lut, reported = info.get("lut"), (info.get("owner"), info.get("name"))
        utype = LIBUNIT_TYPES.get(lut)
        if info.get("name"):
            utype = utype or ds.unit_type(info["owner"], info["name"])
        elif self.target and self._in_target(ds, info["line"]):
            utype, info["owner"], info["name"] = self.target
        if utype:
            info["utype"] = utype
            if not self.warned:
                self.warned = True
                self.message.emit(f"Oracle no indicó la unidad de la pausa (tipo={lut}, programa={reported}); "
                                  f"se asume {info['owner']}.{info['name']} ({utype.lower()}).")
        return info

    def _in_target(self, ds, line):
        """¿La pausa sin nombre de programa es en el código depurado o en el bloque de prueba?
        La pila de DBMS_DEBUG trae el texto de la línea actual: se compara con ambos."""
        current = backtrace_line(ds.backtrace())
        if current and current[0] == line and current[1]:
            if self.target_source is None:
                kind, owner, name = self.target
                self.target_source = ds.source(owner, name, kind).split("\n")
            in_target = line <= len(self.target_source) and same_code(current[1], self.target_source[line - 1])
            in_block = line <= self.block_lines and same_code(current[1], self.block_text[line - 1])
            if in_target != in_block:
                return in_target
        return line > self.block_lines          # sin texto útil: una línea fuera del bloque es del objeto

    # --- sesión que depura (nueva)
    def _debugger(self, st, compile_targets):
        ds = None
        error = None
        try:
            if self.db.opener is None:
                raise RuntimeError("No hay datos para abrir otra sesión; vuelve a conectarte.")
            ds = self.session_factory(self.db.opener())
            for otype, owner, name in compile_targets:
                try:
                    ds.compile_debug(otype, owner, name)
                except Exception as e:
                    self.message.emit(f"Aviso: no se pudo compilar con DEBUG {owner}.{name}: {e}")
            st["ready"].set()
            self.message.emit("Esperando a la sesión principal…")
            status, sid = st["sid"].get(timeout=600)
            if status != "ok":
                raise RuntimeError(sid)
            ds.attach(sid)
            try:
                ds.set_timeout(self.WAIT_S)
            except Exception as e:
                self.message.emit(f"Aviso: no se pudo limitar la espera del depurador: {e}")
            info = self._wait_event(ds, ds.synchronize())
            for key in list(self.bps):
                try:
                    self.bps[key] = ds.set_breakpoint(*key)
                except Exception as e:
                    self.message.emit(str(e))
            info = self._step(ds, "continue" if any(v is not None for v in self.bps.values()) else "into")
            stopped_in_code = False
            while not info["done"] and not st["block_done"].is_set():
                stopped_in_code = True
                info["variables"] = ds.variables(variable_candidates(
                    ds.source(info["owner"], info["name"], info["utype"]) if info["utype"] else ""))
                info["backtrace"] = ds.backtrace()
                self.running = False
                self.stopped.emit(info)
                cmd = self.commands.get()
                while isinstance(cmd, tuple):        # cambios de breakpoints mientras está en pausa
                    _bp, key, added = cmd
                    try:
                        if added:
                            self.bps[key] = ds.set_breakpoint(*key)
                        elif self.bps.get(key) is not None:
                            ds.delete_breakpoint(self.bps.pop(key))
                    except Exception as e:
                        self.message.emit(str(e))
                    cmd = self.commands.get()
                self.running = True
                info = self._step(ds, cmd)
            if not stopped_in_code and not st["abort"]:
                self.message.emit("No se detuvo dentro del código. Revisa que esté compilado con información de "
                                  "depuración (marca «Compilar con información de depuración» al iniciar) y que "
                                  "el bloque de prueba llame al procedimiento.")
        except Exception as e:
            error = str(e)
            st["abort"] = True
            st["ready"].set()
            if ds is not None and st["started"]:
                try:
                    ds.step("abort")
                except Exception:
                    self.db.cancel()              # sin depurador, que la sesión principal no quede esperando
        finally:
            if ds is not None:
                ds.detach()
            st["detached"].set()
            if ds is not None:
                if st["started"] and not st["target_done"].wait(self.TARGET_WAIT_S):
                    # red de seguridad: la sesión principal nunca debe quedar retenida por la depuración
                    self.message.emit("La sesión principal no terminó; se canceló su llamada para liberarla.")
                    self.db.cancel()
                    st["target_done"].wait(10)
                ds.close()
            result = dict(st["result"])
            if error and "error" not in result:
                result["error"] = error
            self.active = self.running = False
            self.ended.emit(result)
