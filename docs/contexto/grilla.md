# Grilla de resultados: edición, filtro y orden

Pruebas: `tests/test_grid.py`, `test_results.py`. Captura: `docs/img/edicion.png`.

## Flujo
1. `SheetWidget.run` → `editable_target(sql)` (sql/editable.py). Si hay destino, ejecuta con
   `with_rowid=True` → `OracleSession.execute_with_rowid`: envuelve con `rowid_query`; si Oracle la
   rechaza, corre la original y marca `res["not_editable"]`. El ROWID sale de las columnas a `res["rowids"]`.
2. `ResultPanel.show_result(res, sql)` (ui/result_panel.py): con rowids crea `EditableResultModel`, pide
   en metadatos `resolve_object` (debe ser TABLE) y `fetch_columns_info` → `model.set_columns`.
   Estado: "✎ OWNER.TABLA" (`panel.target = (owner, table)`) o "🔒 Solo lectura: motivo".
3. Edición en `EditableResultModel` (ui/results.py): `setData` valida con `parse_value(text, ColumnInfo)`;
   si falla emite `rejected(msg)`. Filas `GridRow` con `rowid`, `original`, `deleted`, `new`.
4. `ResultPanel.save()` (Ctrl+S): `model.pending_ops()` → `confirm_save` muestra `SavePreviewDialog`
   (ui/save_preview.py) con las sentencias de `preview_sql` →
   `runner.run(session.apply_grid_changes)` → `model.mark_saved(rowids)` y `conn_tab.set_pending(True)`.
   `cancel()` → `model.revert()`.

## db/grid.py
- `ColumnInfo` (ALL_TAB_COLS): tipo, largo, precisión/escala, char_used, nullable, has_default,
  `readonly_reason` (virtual, identidad, BLOB, LONG…). `describe()` para el tooltip del encabezado.
- `parse_value`: vacío → NULL; NOT NULL; largo en chars o bytes; NUMBER(p,s) (acepta coma decimal);
  fechas `DATE_FORMATS`; RAW en hexadecimal.
- `apply_changes(cur, owner, table, ops)`: `SAVEPOINT minisql_grilla`; UPDATE/DELETE
  `WHERE ROWID = CHARTOROWID(:rid)` con `_expect_one` (la fila debe existir); INSERT con
  `RETURNING ROWIDTOCHAR(ROWID) INTO :rid`. Ante error: `ROLLBACK TO SAVEPOINT` y se relanza.
- `preview_sql(owner, table, ops)` / `sql_literal(v)`: las mismas sentencias con los valores como literales,
  **solo para mostrar** (la ejecución sigue con binds). Pruebas: monkeypatch de `panel.confirm_save`.

## Paginación
- `OracleSession.execute` trae solo `PAGE_ROWS` (200, config.py). Si llenó la página, `res["more"]` y
  `res["stream"]` = `RowStream` (db/session.py): el cursor queda abierto; `fetch(n)` / `fetch_all(limit)` toman el
  candado de la sesión; se cierra solo al llegar al final. DBMS_OUTPUT se lee con otro cursor.
- `ResultModel(page_size=…)`: `all_rows` (cargadas) → `filtered` (filtro) → `rows` (página). `set_page`,
  `page_count`, `first_number` (numeración global), `append_rows` (antes de las filas nuevas). Sin `page_size`
  (tablas del visor) no hay páginas.
- `ResultPanel`: ◀ ▶ (▶ trae de Oracle si hace falta), «⏬ Todas» (hasta `MAX_ROWS` = 100.000), contador
  "201-400 · 400+ filas". `_close_stream()` al mostrar otro resultado y `dispose()` al cerrar la hoja o el visor.
  Los scripts muestran solo la primera página (sin cursor abierto).
- Tras Guardar, `ConnectionTab.transaction_ended("Commit"|"Rollback")` cambia el aviso "pendiente de Commit".
- Pruebas: `tests/test_paging.py` (BIG_TABLE de 450 filas en FakeCursor).

## Filtro y orden (ui/results.py)
- `ResultModel.all_rows` (todas) y `rows` (visibles). `set_filter(texto, columna)` con `make_matcher`:
  texto (contiene), `=v`, `!=v`, `>`, `<`, `>=`, `<=` (números y fechas), `null`, `!null`.
- `sort` ordena `all_rows` con `sort_key` (None al final) y reaplica el filtro.
- `display_text`/`edit_text`: RAW en hex, BLOB `<N bytes>`, DATE sin 00:00:00.

## Reglas
- Siempre por ROWID y con bind variables. **Nunca COMMIT al guardar** (queda pendiente para el usuario).
- Las ediciones sin guardar no van al guardián de sesión: confirmar antes de perderlas (otra ejecución,
  cerrar hoja/conexión/app) con `ResultPanel.has_unsaved_changes()`.
- La pestaña Datos del visor usa el mismo `ResultPanel` (`SELECT * FROM owner.tabla`).
