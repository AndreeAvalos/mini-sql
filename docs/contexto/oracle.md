# db/ — hablar con Oracle

python-oracledb en modo **thin** (sin Instant Client). Corre en hilos de trabajo; **nunca importa Qt**.
Pruebas: `tests/test_session.py`, `test_objects.py`, `test_guard.py`, con `FakeConn/FakeCursor`.

## session.py — OracleSession
- `OracleSession.open(user, password, dsn)` abre **dos conexiones**: `conn` (principal: hojas, transacción
  del usuario, pestaña Datos, código depurado) y `meta` (diccionario, autocompletado, explorador).
  `opener` guarda cómo abrir una tercera (depurador); la contraseña solo vive ahí, en memoria.
- Candados: `lock` (principal) y uno de metadatos. `busy`, `wait_free(timeout)`; si la principal está
  ocupada se lanza `SessionBusy` en vez de esperar.
- `execute(sql, on_output)` → dict `{headers, rows, more, stream, elapsed, message, txn…}`; la primera página
  (`PAGE_ROWS`); `stream` (RowStream) trae las siguientes (ver grilla.md). `execute_with_rowid` agrega `rowids` (ver grilla.md).
  `execute_script(sentencias)`: varias en orden con un solo candado; para en la primera que falla
  (`ScriptError` dice cuál y si las anteriores quedaron pendientes de Commit).
- `apply_grid_changes(owner, table, ops)`, `commit`, `rollback`, `cancel` (cancela la llamada en curso),
  `with_meta_cursor(fn)`, `transaction_in_progress`, `close`.
- `disconnect()` / `reconnect()` (con `opener`, en un hilo; `SessionBusy` si hay algo corriendo) y `connected`. Sin conexión, `execute`, `execute_script`, `with_meta_cursor`, commit/rollback y la grilla lanzan `NotConnected`. `connection_lost(error)`: ORA-03113/03114/03135/02396/00028/01012, DPY-1001/4011, DPI-1080. `user` se recuerda (no se lee de una conexión cerrada).
- `read_dbms_output(cur)`: lee DBMS_OUTPUT por bloques; `output_enabled` evita habilitarlo cada vez.
- Autocommit apagado: el usuario decide Commit/Rollback.

## catalog.py — diccionario
- `list_schemas`, `list_objects(owner, otype)`, `list_columns`, `list_subprograms`, `completion_objects`.
- `describe_object` / `resolve_object(cur, me, owner, name)`: sigue sinónimos (`_synonym_target`) y
  devuelve (owner, name, tipo). Lo usa F4 y la grilla editable.
- `fetch_arguments(owner, name, subprogram)` (ALL_ARGUMENTS para la plantilla de depuración).
- `kind_label`, `format_type(dtype, length, precision, scale, char_length)`.

## source.py
- `fetch_source`, `fetch_ddl` (DBMS_METADATA), `fetch_errors`, `compile_code(cur, code, owner, me)`.

## object_tabs.py
- Pestañas del visor registradas con `@tab_loader("Nombre")`; devuelven `Grid(headers, rows)` o
  `Text(...)`. Para una pestaña nueva: cargador aquí + nombre en `OBJECT_TABS` (ui/object_viewer.py).

## debug.py, grid.py, tns.py
- `DebugSession`: ver depuracion.md. `grid.py`: ver grilla.md.
- `parse_tnsnames(path)`: comentarios, alias múltiples, IFILE; `default_tns_path()` (TNS_ADMIN, ORACLE_HOME).

## Reglas
- **Bind variables siempre** (`:own`, `:n`…) en consultas al diccionario; nunca concatenar nombres.
  Si hay que generar PL/SQL con nombres, citarlos con `q()`.
- Nunca COMMIT implícito. Nunca ejecutar DML que el usuario no escribió (salvo la grilla, por ROWID).
- Compatibilidad: Oracle 11g a 23ai (p. ej. `identity_column` no existe en 11g: hay respaldo).
- Las pruebas reconocen consultas por texto en minúsculas (`"from all_tab_cols" in q`): si cambias una
  consulta del diccionario, revisa `tests/support.py` y `docs/generar_capturas.py` (DemoCursor).
