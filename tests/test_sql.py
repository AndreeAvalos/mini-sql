"""Funciones puras de texto SQL, PL/SQL y tnsnames."""

from minisql.db.catalog import resolve_object
from minisql.db.session import read_dbms_output
from minisql.db.tns import parse_tnsnames
from minisql.sql.completion import completion_target, make_alias, rank_completions, table_refs, wants_alias
from minisql.sql.plsql import (
    backtrace_line,
    debug_template,
    debug_wrapper,
    editor_line,
    map_errors,
    same_code,
    split_units,
    unit_at_line,
    unit_info,
    variable_candidates,
)
from minisql.sql.text import (
    clean_statement,
    first_keyword,
    format_sql,
    inside_literal,
    sqlplus_to_sql,
    statement_at_cursor,
    word_at,
)

from support import PKG_CODE, FakeCursor


def test_sql_helpers():
    assert clean_statement("select 1 from dual;") == "select 1 from dual"
    assert clean_statement("begin null; end;\n/") == "begin null; end;"
    assert statement_at_cursor("a;\n\nb;", 0) == "a;"
    assert statement_at_cursor("a;\n\nb;", 5) == "b;"
    assert first_keyword("-- comentario\n  delete from t") == "DELETE"


def test_format_sql():
    out = format_sql("select a,b from t where x=1 and y=2")
    assert out.splitlines()[0].startswith("SELECT a")
    assert "\nFROM t" in out and "\nWHERE x = 1" in out
    assert "\n\n" not in format_sql("select * from (select a from t) x where a in (select b from u)")


def test_completion_helpers():
    assert completion_target("select e.nom") == (["E"], "nom")
    assert completion_target("select * from hr.emp.") == (["HR", "EMP"], "")
    assert completion_target('select "Mi".') == (["Mi"], "")
    assert inside_literal("select 'abc") and inside_literal("x -- com")
    assert not inside_literal("select 'a''b' /* c */ from")
    refs = table_refs("select a, b c from emp e, hr.dept d join x on 1=1 where y in (1, 2)")
    assert refs["E"] == (None, "EMP") and refs["D"] == ("HR", "DEPT") and refs["X"] == (None, "X")
    assert "C" not in refs and "ON" not in refs
    ranked = rank_completions([("NOMBRE", "", True, 1), ("NO", "", False, 5), ("ID_NOM", "", True, 0)], "nom")
    assert [r[0] for r in ranked] == ["NOMBRE", "ID_NOM"]


def test_alias_helpers():
    assert wants_alias("select * from em") and wants_alias("select * from emp e join hr.de")
    assert wants_alias("select * from emp e, de") and wants_alias("update em")
    assert not wants_alias("select em") and not wants_alias("select a, b") and not wants_alias("insert into em")
    assert make_alias("CLIENTE_DIRECCION", set()) == "cd"
    assert make_alias("EMP", {"E"}) == "e2"
    assert make_alias("ORDEN_NOTA", set()) == "on2"       # ON es palabra clave


def test_parse_tnsnames(tmp_path):
    (tmp_path / "extra.ora").write_text("QA = (DESCRIPTION=(ADDRESS=(HOST=qa)))\n")
    tns = tmp_path / "tnsnames.ora"
    tns.write_text(
        "# comentario\nPROD, PROD.WORLD =\n  (DESCRIPTION =\n    (ADDRESS = (HOST = srv)(PORT = 1521)))\n"
        "IFILE = extra.ora\nDESA = (DESCRIPTION=(ADDRESS=(HOST=des)))\n"
    )
    entries = parse_tnsnames(tns)
    assert set(entries) == {"PROD", "PROD.WORLD", "QA", "DESA"}
    assert "srv" in entries["PROD"]


def test_resolve_object():
    cur = FakeCursor()
    assert resolve_object(cur, "SCOTT", None, "EMP") == ("SCOTT", "EMP", "TABLE")
    assert resolve_object(cur, "SCOTT", None, "S_EMP") == ("SCOTT", "EMP", "TABLE")   # sigue el sinónimo
    assert resolve_object(cur, "SCOTT", "SCOTT", "PKG") == ("SCOTT", "PKG", "PACKAGE")


def test_plsql_units():
    units = split_units(PKG_CODE)
    assert [u[0] for u in units] == [1, 6]
    assert unit_info(units[1][1], "SCOTT") == ("PACKAGE BODY", "SCOTT", "PKG")
    assert unit_info("create or replace editionable procedure hr.p is begin null; end;", "X") == \
        ("PROCEDURE", "HR", "P")
    assert unit_at_line(PKG_CODE, 9, "SCOTT") == ("PACKAGE BODY", "SCOTT", "PKG", 4)
    assert unit_at_line(PKG_CODE, 4, "SCOTT") is None            # la línea con "/"
    assert editor_line(PKG_CODE, "PACKAGE BODY", "PKG", 4, "SCOTT") == 9
    errs = map_errors(PKG_CODE, [("PACKAGE BODY", 3, 5, "PLS-00103: x  ")], "SCOTT")
    assert errs == [(8, 5, "PLS-00103: x")]


def test_debug_template():
    args = [(None, 0, "OUT", "NUMBER", None, None, None),
            ("P_ID", 1, "IN", "NUMBER", None, None, None),
            ("P_NOM", 2, "IN/OUT", "VARCHAR2", None, None, None)]
    block = debug_template("SCOTT", "PKG", "CALC", args)
    assert "P_NOM VARCHAR2(32767) := NULL;" in block
    assert "v_resultado := SCOTT.PKG.CALC(P_ID => P_ID, P_NOM => P_NOM);" in block
    assert debug_template("SCOTT", None, "LIMPIAR", [(None, 1, "IN", None, None, None, None)]) == \
        "BEGIN\n  SCOTT.LIMPIAR;\nEND;"
    assert variable_candidates("v_total := v_total + 1; -- comentario x\nselect 'y' into v_n from dual;") == \
        ["V_TOTAL", "V_N", "DUAL"]


def test_read_dbms_output_in_chunks():
    cur = FakeCursor()
    FakeCursor.buffer = [f"línea {i}" for i in range(250)]
    lines = read_dbms_output(cur, chunk=100)
    assert len(lines) == 250 and lines[0] == "línea 0" and lines[-1] == "línea 249"
    assert FakeCursor.buffer == []
    FakeCursor.buffer = [str(i) for i in range(50)]
    assert len(read_dbms_output(cur, limit=10, chunk=4)) == 10
    FakeCursor.buffer = []


def test_sqlplus_commands():
    assert sqlplus_to_sql("set serveroutput on") == (True, "")
    assert sqlplus_to_sql("SET SERVEROUTPUT OFF") == (False, "")
    assert sqlplus_to_sql("set serveroutput on size unlimited\nexec pkg.alta(1, 'x')") == \
        (True, "BEGIN\n  pkg.alta(1, 'x');\nEND;")
    assert sqlplus_to_sql("EXECUTE limpiar;") == (None, "BEGIN\n  limpiar;\nEND;")
    assert sqlplus_to_sql("execute immediate 'x'") == (None, "execute immediate 'x'")
    assert sqlplus_to_sql("select 1 from dual") == (None, "select 1 from dual")


def test_word_at():
    assert word_at("  v_total := v_total + 1;", 4) == "v_total"
    assert word_at("  v_total := 1;", 10) == ""
    assert word_at("x$ab#1 := 2", 0) == "x$ab#1"


def test_debug_wrapper_and_stack_text():
    wrapped = debug_wrapper("BEGIN\n  pkg.alta;\nEND;")
    assert wrapped.startswith("BEGIN\n  BEGIN\n    BEGIN\n      pkg.alta;")
    assert "EXCEPTION WHEN OTHERS THEN\n    DBMS_DEBUG.DEBUG_OFF;\n    RAISE;" in wrapped
    assert wrapped.endswith("  DBMS_DEBUG.DEBUG_OFF;\nEND;")
    assert debug_wrapper("pkg.alta").count("pkg.alta;") == 1          # agrega el ; que falte
    assert backtrace_line("[Line 15]          DBMS_OUTPUT.PUT_LINE('x');\n<source not available>") == \
        (15, "DBMS_OUTPUT.PUT_LINE('x');")
    assert backtrace_line("<source not available>") is None
    assert same_code("  RETURN   V_RESULTADO;", "RETURN V_RESULTADO;")
    assert same_code("SELECT LISTAGG(RV_LOW_VALUE, ',') WITHIN", "SELECT LISTAGG(RV_LOW_VALUE, ',') WITHIN GROUP")
    assert not same_code("", "END;") and not same_code("NULL;", "END;")
