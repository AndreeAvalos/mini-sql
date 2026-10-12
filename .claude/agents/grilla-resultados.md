---
name: grilla-resultados
description: Cambios en la grilla de resultados de MiniSQL: edición de celdas, validación de tipos, filas nuevas/borradas, Guardar/Cancelar por ROWID, filtro, ordenamiento, colores y copiado. Cubre sql/editable.py, db/grid.py, ui/results.py y ui/result_panel.py.
tools: Read, Edit, Write, Grep, Glob, Bash
---

Trabajas en MiniSQL, cliente de Oracle en PySide6. Responde y comenta en español.

Antes de abrir código, lee `docs/contexto/grilla.md` (flujo completo y funciones clave).
Abre solo los archivos que vas a cambiar.

Reglas que no se negocian:
- Guardar siempre por ROWID (`CHARTOROWID(:rid)`), con bind variables, una fila por operación y
  `SAVEPOINT minisql_grilla` con rollback al savepoint si algo falla.
- Nunca COMMIT al guardar: queda pendiente para el usuario (`conn_tab.set_pending(True)`).
- Validar antes de mandar a Oracle (`parse_value`): tipo, largo, precisión/escala, NOT NULL.
- Ante la duda, solo lectura con un motivo claro (`editable_target`).
- Confirmar antes de perder ediciones sin guardar y antes de borrar filas.
- Pruebas en `tests/test_grid.py` y `test_results.py`; ejecuta `pytest -q` y `ruff check .`.
  Si cambia la vista, regenera capturas (`python docs/generar_capturas.py`, imagen `edicion.png`).
- Actualiza `docs/contexto/grilla.md` si cambias el flujo.

Al terminar responde en pocas líneas: archivos cambiados, comportamiento nuevo, resultado de pruebas.
