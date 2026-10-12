# ui/ — interfaz Qt (PySide6)

Pruebas: `tests/test_sheet.py`, `test_app.py`, `test_objects.py`, `test_find.py`, `test_results.py`
(Qt `offscreen`, fixture `app` en conftest.py).

## Jerarquía
    MainWindow (main_window.py)         pestañas de conexión, atajos globales, closeEvent, restaurar sesión
      ConnectionTab (connection_tab.py) explorador + pestañas de hojas/visores + barra Commit/Rollback
        ObjectExplorer (explorer.py)    árbol esquema > categoría > objeto, carga perezosa, filtro, menú
        SheetWidget (sheet.py)          editor + ResultPanel + DBMS_OUTPUT + estado
        ObjectViewer (object_viewer.py) pestañas por tipo (OBJECT_TABS); "Código" = CodePage; "Datos" = ResultPanel

## Hilos (regla principal)
- `TaskRunner.run(fn, on_ok, on_err)` (workers.py): fn corre en un hilo, los callbacks en el hilo de Qt.
- `ConnectionTab.meta_call(fn(cursor), on_ok, on_err)`: consulta en la sesión de metadatos.
- `ConnectionTab.run_statement(sql, emitter, with_rowid=False)`: sesión principal; `Emitter` tiene
  `done(dict)`, `failed(str)`, `output(list)`. `after_statement(res)` actualiza "cambios sin confirmar".
- Nunca tocar widgets desde un hilo; nunca llamar a Oracle desde el hilo de la interfaz.

## SheetWidget.run()
1. Si la grilla tiene cambios sin guardar, pregunta. 2. Toma la selección o la sentencia del cursor
(`statement_range`) y la separa con `split_script`. 3. `confirm(sentencias)`: UPDATE/DELETE sin WHERE, DROP,
TRUNCATE, DDL con transacción pendiente o con DML antes en el script. 4. Una sentencia: `editable_target` decide
`with_rowid`; varias: `ConnectionTab.run_script` → `OracleSession.execute_script` (se detiene en la primera que
falla con `ScriptError`; `on_error` llama `refresh_pending`). 5. `show_running_statement`
(capa "run"), spinner, `on_output`, `on_done` → `results.show_result(res, sql)`.

## Editores (editors.py)
- `SqlEditor`: resaltado (highlighter.py), autocompletado (`CompletionProvider` en completion.py con caché
  de metadatos), F4 abre el objeto bajo el cursor. Resaltados por capas: `set_highlights(capa, selecciones)`
  con `LAYER_ORDER = ("run", "exec", "find", "find_current")`.
- `CodeEditor(SqlEditor)`: margen con números y breakpoints, `set_exec_line`, `set_debug_values`/`value_at`
  (tooltip con valor de variable), `go_to_line`.
- `find_bar.py`: `with_find_bar(editor)` devuelve el contenedor; Ctrl+F, Ctrl+H, F3, Shift+F3, Esc.

## Otros
- `ConnectionTab.current_sheet()` siempre devuelve una SheetWidget (nunca un visor).
- `style.py`: `mono_font`, `is_dark`, `StatusLabel`, `RESULT_THEME` (claro/oscuro) y `result_stylesheet`.
- `connect_dialog.py`: perfiles (`config.load_profiles`), contraseña en keyring (`KEYRING_SERVICE`),
  tnsnames. Las pruebas lo reemplazan con `monkeypatch.setattr(main_window, "ConnectDialog", ...)`.
- `app.py`: `single_instance_lock()` (QLockFile en ~/.minisql/minisql.lock) y `main()`.

## Reglas
- Textos de la interfaz en español. Confirmar antes de perder trabajo (hojas, código, grilla).
- Si cambia algo visible, regenerar capturas: `python docs/generar_capturas.py`.
