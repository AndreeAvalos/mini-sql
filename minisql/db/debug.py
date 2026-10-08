"""Llamadas a DBMS_DEBUG desde la sesión depuradora."""

import contextlib

from ..sql.text import q

RUNTIME_OUT = """
  :ret := ret;
  :tmo := case when ret = dbms_debug.error_timeout then 1 else 0 end;
  :reason := ri.reason;
  :line := ri.line#;
  :lut := ri.program.libunittype;
  :owner := ri.program.owner;
  :name := ri.program.name;
  :utype := case ri.program.libunittype
              when dbms_debug.libunittype_procedure then 'PROCEDURE'
              when dbms_debug.libunittype_function then 'FUNCTION'
              when dbms_debug.libunittype_package then 'PACKAGE'
              when dbms_debug.libunittype_package_body then 'PACKAGE BODY'
              when dbms_debug.libunittype_trigger then 'TRIGGER'
              else null end;
  :done := case when ri.terminated = 1
                  or ri.reason in (dbms_debug.reason_exit, dbms_debug.reason_knl_exit) then 1 else 0 end;"""


class DebugSession:
    """Llamadas a DBMS_DEBUG desde la sesión depuradora. Corre en un hilo de trabajo.
    Los registros de DBMS_DEBUG no se pueden enlazar desde Python, así que cada llamada
    va en un bloque anónimo que devuelve los campos como bind variables."""

    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()

    def _runtime(self, body, **binds):
        out = {k: self.cur.var(int) for k in ("ret", "tmo", "reason", "line", "done", "lut")}
        out.update({k: self.cur.var(str, 128) for k in ("owner", "name", "utype")})
        self.cur.execute(f"""
            declare
              ri dbms_debug.runtime_info;
              ret binary_integer;
            begin
              {body}
              {RUNTIME_OUT}
            end;""", **binds, **out)
        res = {k: v.getvalue() for k, v in out.items()}
        if res["tmo"]:
            return {"timeout": True}      # no hubo evento en el tiempo de espera: el código sigue corriendo
        if res["ret"] not in (0, None):
            raise RuntimeError(f"DBMS_DEBUG devolvió el código de error {res['ret']}")
        return res

    def compile_debug(self, otype, owner, name):
        self.cur.execute(f"alter {otype.lower()} {q(owner)}.{q(name)} compile debug")

    def attach(self, sid):
        self.cur.execute("begin dbms_debug.attach_session(:sid); end;", sid=sid)

    def set_timeout(self, seconds):
        """Cuánto espera synchronize/continue un evento antes de devolver error_timeout."""
        self.cur.execute("declare t binary_integer; begin t := dbms_debug.set_timeout(:s); end;", s=seconds)

    def synchronize(self):
        return self._runtime("ret := dbms_debug.synchronize(ri, dbms_debug.info_getlineinfo);")

    def step(self, cmd):
        """cmd: continue, over, into, out o abort."""
        return self._runtime("""
              ret := dbms_debug.continue(ri,
                       case :cmd
                         when 'over' then dbms_debug.break_next_line
                         when 'into' then dbms_debug.break_any_call + dbms_debug.break_next_line
                         when 'out' then dbms_debug.break_any_return
                         when 'abort' then dbms_debug.abort_execution
                         else 0 end,
                       dbms_debug.info_getlineinfo);""", cmd=cmd)

    def set_breakpoint(self, kind, owner, name, line):
        bp, ret = self.cur.var(int), self.cur.var(int)
        self.cur.execute("""
            declare
              pi dbms_debug.program_info;
              bp binary_integer;
            begin
              pi.namespace := case :kind
                                when 'PACKAGE BODY' then dbms_debug.namespace_pkg_body
                                when 'TYPE BODY' then dbms_debug.namespace_pkg_body
                                when 'TRIGGER' then dbms_debug.namespace_trigger
                                else dbms_debug.namespace_pkgspec_or_toplevel end;
              pi.owner := :owner;
              pi.name := :name;
              pi.dblink := null;
              :ret := dbms_debug.set_breakpoint(pi, :line, bp);
              :bp := bp;
            end;""", kind=kind, owner=owner, name=name, line=line, bp=bp, ret=ret)
        if ret.getvalue() != 0:
            raise RuntimeError(f"No se pudo poner el punto de interrupción en {name} línea {line} "
                               f"(código {ret.getvalue()}). ¿Está compilado con DEBUG?")
        return bp.getvalue()

    def delete_breakpoint(self, bp):
        self.cur.execute("declare r binary_integer; begin r := dbms_debug.delete_breakpoint(:bp); end;", bp=bp)

    def variables(self, names):
        """[(nombre, valor)] de las variables visibles en el marco actual."""
        if not names:
            return []
        res = self.cur.var(str, 32767)
        self.cur.execute("""
            declare
              names varchar2(32767) := :names;
              res varchar2(32767);
              v varchar2(4000);
              n varchar2(128);
              p pls_integer := 1;
              c pls_integer;
              ret binary_integer;
            begin
              loop
                c := instr(names, ',', p);
                exit when c = 0 or nvl(length(res), 0) > 30000;
                n := substr(names, p, c - p);
                p := c + 1;
                begin
                  ret := dbms_debug.get_value(n, 0, v, null);
                  if ret = dbms_debug.success then
                    res := res || n || chr(1) || substr(nvl(v, '(null)'), 1, 500) || chr(2);
                  end if;
                exception when others then null;
                end;
              end loop;
              :res := res;
            end;""", names=",".join(names) + ",", res=res)
        pairs = []
        for item in (res.getvalue() or "").split("\x02"):
            if "\x01" in item:
                pairs.append(tuple(item.split("\x01", 1)))
        return pairs

    def backtrace(self):
        out = self.cur.var(str, 32767)
        self.cur.execute("declare l varchar2(32767); begin dbms_debug.print_backtrace(l); :bt := l; end;", bt=out)
        return (out.getvalue() or "").strip()

    def unit_type(self, owner, name):
        """Tipo de unidad de un objeto con código, para cuando Oracle no lo informa al detenerse.
        En un paquete o tipo, el código que corre está en el cuerpo."""
        self.cur.execute("""
            select object_type from all_objects
            where owner = :own and object_name = :nam
              and object_type in ('PROCEDURE', 'FUNCTION', 'PACKAGE', 'TRIGGER', 'TYPE')""",
                         own=owner, nam=name)
        row = self.cur.fetchone()
        if not row:
            return None
        return {"PACKAGE": "PACKAGE BODY", "TYPE": "TYPE BODY"}.get(row[0], row[0])

    def source(self, owner, name, utype):
        self.cur.execute("select text from all_source where owner = :own and name = :nam and type = :typ order by line",
                         own=owner, nam=name, typ=utype)
        return "".join(r[0] for r in self.cur.fetchall())

    def detach(self):
        with contextlib.suppress(Exception):
            self.cur.execute("begin dbms_debug.detach_session; end;")

    def close(self):
        with contextlib.suppress(Exception):
            self.conn.close()
