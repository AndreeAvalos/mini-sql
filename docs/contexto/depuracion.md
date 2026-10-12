# Código PL/SQL y depuración (DBMS_DEBUG)

Pruebas: `tests/test_debugger.py`, `test_objects.py`. Reemplazo del depurador real:
`PlsqlDebugger.session_factory = FakeDebugSession` (tests/support.py, con `script` de líneas y `variables`).

## Editar y compilar
- `ui/code_page.py` `CodePage`: CodeEditor + errores; `compile()` → `db/source.compile_code`, errores de
  ALL_ERRORS mapeados con `sql/plsql.map_errors` (unidades separadas por `/`). `has_unsaved`,
  `restore_code` (el código sin compilar entra al snapshot de la sesión).
- `start_debug()` arma el bloque con `debug_template` (ALL_ARGUMENTS) y abre `DebugStartDialog`.

## Sesiones que intervienen
- **Principal** (`_target`, hilo): `DBMS_DEBUG.INITIALIZE` + `DEBUG_ON` + `DBMS_OUTPUT.ENABLE`, ejecuta el
  bloque envuelto con `debug_wrapper` (DEBUG_OFF en la misma llamada, haya error o no). Sus cambios quedan
  pendientes de Commit como cualquier ejecución.
- **Depuradora** (`_debugger`, hilo, sesión nueva vía `opener`): `DebugSession` (db/debug.py):
  `compile_debug`, `attach(sid)`, `set_timeout`, `synchronize`, `step(cmd)`, `set_breakpoint`,
  `variables(names)`, `backtrace`, `unit_type`, `source`, `detach`, `close`.
- La interfaz manda órdenes por una cola (`command("continue" | "over" | "into" | "out")`, `stop()`,
  `breakpoint_changed`) y recibe señales `stopped(dict)`, `resumed`, `message`, `ended(dict)`.

## Puntos delicados (ya resueltos, no romper)
- Oracle 19c a veces no informa la unidad detenida: se ubica con `target`, `backtrace_line` y `same_code`
  comparando el texto de la línea.
- `_wait_event`: si no hay evento en `WAIT_S` y el bloque ya terminó (`block_done`), se cierra la depuración.
  Al terminar se espera `TARGET_WAIT_S` a la principal y si no, se cancela (evita el deadlock).
- El marcador de línea (capa "exec") **solo se muestra mientras está en pausa**; `resumed` lo limpia.
- Re-ejecutar: `active` debe volver a False al terminar o detener; `stop()` antes de iniciar aborta limpio.
- Valores al pasar el mouse: `variable_candidates` + `DebugSession.variables` → `CodeEditor.set_debug_values`.
- DBMS_OUTPUT del bloque se lee al final (`read_dbms_output`) y se muestra en el panel.

## UI
- `ui/debug_panel.py`: `DebugPanel` (botones, pila, variables, log), `DebugStartDialog`,
  `DebugController` (une `PlsqlDebugger` con visores: `show_exec_line`, `clear_exec_line`).
