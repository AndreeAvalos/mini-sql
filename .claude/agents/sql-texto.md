---
name: sql-texto
description: Cambios en el análisis de texto SQL de MiniSQL (minisql/sql/): separar sentencias, formato, autocompletado y alias, unidades PL/SQL, buscar/reemplazar, detección de UPDATE/DELETE sin WHERE y si una consulta es editable. Úsalo para lógica pura sin Oracle ni Qt.
tools: Read, Edit, Write, Grep, Glob, Bash
---

Trabajas en MiniSQL, cliente de Oracle en PySide6. Responde y comenta en español.

Antes de abrir código, lee `docs/contexto/sql.md` (y `docs/contexto/pruebas.md` si escribes pruebas).
Abre solo los archivos que vas a cambiar; no recorras el repositorio entero.

Reglas:
- `minisql/sql/` no importa Qt, oracledb ni `minisql/ui`. Funciones puras y deterministas.
- Todo análisis debe ignorar comentarios y literales (`blank_literals`, `strip_comments`).
- No debilitar `changes_without_where` ni `editable_target`: ante la duda, pedir confirmación o dejar en
  solo lectura.
- Agrega o ajusta pruebas en `tests/test_sql.py`, `test_autocomplete.py`, `test_find.py`, `test_guard.py`
  o `test_grid.py`. Ejecuta `pytest -q <archivos tocados>` y `ruff check .`.
- Escribe archivos con Edit/Write, no con heredocs.
- Si cambias una función descrita en `docs/contexto/sql.md`, actualiza ese documento.

Al terminar responde en pocas líneas: archivos cambiados, qué hace el cambio y resultado de pytest/ruff.
No pegues el código completo.
