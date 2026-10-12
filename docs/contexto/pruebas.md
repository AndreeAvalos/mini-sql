# Pruebas, estilo y capturas

    pytest -q                          # sin Oracle: conexiones simuladas, Qt offscreen
    pytest -q tests/test_grid.py -k save   # un tema
    ruff check .                       # líneas de 120, reglas en pyproject.toml
    python docs/generar_capturas.py    # imágenes del README (abre ventanas unos segundos)

## tests/support.py
- `make_session(conn, meta, opener)` → OracleSession con `FakeConn`.
- `FakeCursor` responde según el texto de la consulta en minúsculas (`self.sql`) y los binds (`self.kw`):
  `select` → columnas A, B y filas `(1,"x"), (2,None)`; `minisql$rowid` → con ROWID `ROW1/ROW2`;
  ALL_TAB_COLS → A NUMBER(10,2) NOT NULL, B VARCHAR2(5); update/delete con `rid="GONE"` → 0 filas;
  `raise_application_error` → error; `put_line('x')` → búfer de DBMS_OUTPUT. Registro: `FakeCursor.executed`.
- `pump(app, n)`, `wait_until(app, cond)`, `run_and_wait(app, sheet, sql)`, `suggestions(...)`,
  `FakeDialog`, `SlowConn`/`start_slow_query` (consultas que tardan), `FakeDebugSession`.
- Para una consulta nueva del diccionario: agregar su respuesta en `FakeCursor.fetchall` y en
  `DemoCursor` de `docs/generar_capturas.py`.

## Archivos por tema
test_sql (texto), test_autocomplete, test_find, test_guard (DML peligroso), test_session, test_objects,
test_sheet, test_results, test_grid, test_debugger, test_app (instancia única, cierre, recuperación),
test_selftest (autoprueba del .exe, keyring solo obligatorio en Windows).

## Reglas
- Cada cambio de comportamiento lleva prueba. `ruff check .` sin avisos.
- Ruff marca caracteres ambiguos (RUF001): no usar ＋, −, etc. en cadenas; usar sus equivalentes ASCII.
- En Windows, escribir archivos con la herramienta de edición, no con heredocs (se rompen `\n` y `\t`).
