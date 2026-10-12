# sql/ — texto SQL puro

Sin Oracle ni Qt: todo se prueba con cadenas. Pruebas: `tests/test_sql.py`, `test_autocomplete.py`,
`test_find.py`, `test_guard.py`, `test_grid.py` (parte de editable).

## text.py
- `strip_comments`, `first_keyword`, `clean_statement` (quita `;` final y comentarios de borde).
- `statement_range(text, pos)` / `statement_at_cursor`: **las sentencias se separan por líneas en blanco**
  (no por `;`), como en SQL Developer con Ctrl+Enter.
- `split_statements(sql)` / `split_script(sql)`: separa un bloque o selección en sentencias como SQL*Plus
  (`;` en SQL; `/` en su línea o el final para PL/SQL), ignorando `;` en cadenas, `q'[…]'`, comentarios y
  `"identificadores"`. `split_script` además devuelve SET SERVEROUTPUT y convierte EXEC. Pruebas: test_script.py.
- `format_sql` (sqlparse, mayúsculas en palabras clave), `norm_ident` (mayúsculas salvo con comillas),
  `q(name)` (cita un identificador para PL/SQL generado).
- `string_end`, `inside_literal`, `blank_literals`: ignorar el contenido de literales `'...'` y `q'[...]'`.
- `sqlplus_to_sql`: quita comandos de SQL*Plus (`SET`, `PROMPT`, `/` sueltos…).
- `word_at(line, col)`: palabra bajo el mouse (valores en depuración).
- `changes_without_where(sql)`: detecta UPDATE/DELETE sin WHERE propio (usa `_has_own_where`, que ignora
  WHERE de subconsultas). La hoja pide confirmación con esto: **no debilitarla**.

## completion.py
- `completion_target(before)`: qué se escribe (`owner.`, `alias.`, palabra suelta).
- `table_refs(statement)`: tablas y alias del FROM/JOIN; `last_clause`.
- `wants_alias`, `make_alias(name, taken)`: alias automático al aceptar una tabla en FROM (`clientes c`).
- `alias_spelling`: respeta mayúsculas/minúsculas del alias escrito; `rank_completions(items, prefix)`.

## plsql.py
- `split_units(code)`: unidades separadas por `/` en su propia línea; `unit_info`, `unit_at_line`,
  `editor_line` (línea de la unidad ↔ línea del editor), `map_errors` (ALL_ERRORS → líneas del editor).
- `plsql_type`, `debug_template(owner, package, sub, args)`: bloque anónimo para depurar un subprograma.
- `debug_wrapper(block)`: envuelve el bloque con `DBMS_DEBUG.DEBUG_OFF` dentro de la misma llamada
  (evita el bloqueo de la sesión principal). `backtrace_line`, `same_code`, `variable_candidates`.

## editable.py
- `editable_target(sql) -> (EditTarget | None, motivo)`: editable solo si es **una tabla**, sin JOIN,
  UNION, GROUP BY, DISTINCT, CONNECT BY, PIVOT, FOR UPDATE, subconsulta en FROM, dblink ni DUAL.
- `EditTarget.source_of(nombre_resultado, columnas_tabla)`: columna real de la tabla o None (calculada).
- `rowid_query(sql)`: `SELECT ROWIDTOCHAR(ROWID) AS "MINISQL$ROWID", q.* FROM (sql) q`.

## search.py
- `SearchOptions` (mayúsculas, palabra completa, regex), `find_matches`, `replace_all`, `replacement_for`.
- `QtPositions`: convierte índices de Python ↔ posiciones UTF-16 de Qt (emojis, acentos combinados).

## Reglas
- Nada de Qt ni oracledb aquí. Funciones puras, deterministas y con prueba.
- Cualquier análisis de texto debe ignorar comentarios y literales (`blank_literals`).
