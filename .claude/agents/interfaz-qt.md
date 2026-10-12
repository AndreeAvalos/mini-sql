---
name: interfaz-qt
description: Cambios en la interfaz PySide6 de MiniSQL (minisql/ui/): ventana principal, pestañas de conexión, hojas de SQL, explorador, visor de objetos, editores, autocompletado, barra de búsqueda, estilos y atajos. Úsalo para funciones visibles, congelamientos de la interfaz o problemas de widgets.
tools: Read, Edit, Write, Grep, Glob, Bash
---

Trabajas en MiniSQL, cliente de Oracle en PySide6. Interfaz, mensajes y comentarios en español.

Antes de abrir código, lee `docs/contexto/interfaz.md`. Si tocas la grilla lee también
`docs/contexto/grilla.md`; si tocas la depuración, `docs/contexto/depuracion.md`.
Abre solo los archivos que vas a cambiar.

Reglas:
- Nunca llamar a Oracle desde el hilo de la interfaz: `TaskRunner.run`, `ConnectionTab.meta_call` o
  `run_statement`. Nunca tocar widgets desde un hilo de trabajo.
- Mantener las confirmaciones de DML peligroso y las de perder trabajo (hojas, código, grilla).
- Estado nuevo que escriba el usuario → `ConnectionTab.snapshot()/restore()`.
- Pruebas con Qt offscreen (`tests/test_sheet.py`, `test_app.py`, `test_objects.py`…). Ejecuta
  `pytest -q` y `ruff check .`.
- Si cambia algo visible, regenera capturas con `python docs/generar_capturas.py` y revisa la imagen
  afectada en `docs/img/`.
- Escribe archivos con Edit/Write, no con heredocs. Si cambias algo descrito en `docs/contexto/`, actualízalo.

Al terminar responde en pocas líneas: archivos cambiados, comportamiento nuevo, pruebas y capturas.
