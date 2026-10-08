# MiniSQL

Cliente de escritorio personal para Oracle, hecho con PySide6 y python-oracledb (modo thin).
La interfaz, los mensajes y los comentarios están en español.

## Ejecutar y probar

    pip install -r requirements.txt -r requirements-dev.txt
    python main.py        # o: python -m minisql, o el comando minisql si se instaló con pip install .
    pytest                # usa conexiones simuladas, no necesita Oracle
    ruff check .          # estilo y errores; configuración en pyproject.toml (líneas de 120)
    python docs/generar_capturas.py   # regenera las imágenes del README con datos simulados

GitHub Actions (`.github/workflows/tests.yml`) corre ruff y pytest en Windows y Linux con Python 3.10 y 3.13.
Dependencias: `pyproject.toml` es la fuente; requirements*.txt las repiten para instalar rápido.

## Estructura

Capas de abajo hacia arriba; una capa nunca importa de una superior.

    minisql/
      config.py          rutas (~/.minisql), límites (MAX_ROWS…), perfiles y ajustes
      workspace.py       espacio de trabajo en disco (WorkspaceStore): sesion/indice.json + una carpeta
                         por conexión y un .sql por hoja; escritura atómica, solo lo que cambió
      sql/               texto puro, sin Oracle ni Qt (fácil de probar)
        text.py            comentarios, separar sentencias (líneas en blanco), identificadores, format_sql
        completion.py      qué se está escribiendo, alias del FROM/JOIN, ranking de sugerencias
        plsql.py           unidades PL/SQL separadas por '/', líneas unidad ↔ editor, plantilla de depuración
      db/                todo lo que habla con Oracle; corre en hilos, sin Qt
        session.py         OracleSession: sesión principal + de metadatos, candados, execute, commit/rollback,
                           cancel, DBMS_OUTPUT (read_dbms_output)
        catalog.py         consultas al diccionario: explorador, autocompletado, resolve_object (F4), argumentos
        source.py          fuente/DDL, compile_code, errores de compilación
        object_tabs.py     cargadores de las pestañas del visor, registrados con @tab_loader("Nombre")
        tns.py             tnsnames.ora (comentarios, alias múltiples, IFILE)
        debug.py           DebugSession: llamadas a DBMS_DEBUG
      ui/                widgets de Qt
        main_window.py     pestañas de conexiones, atajos globales, recuperación de sesión al abrir
        connection_tab.py  una conexión: hojas, visores, transacción pendiente, cierre
        sheet.py           hoja de SQL: ejecutar, formatear, F4, resultados y DBMS_OUTPUT
        explorer.py        árbol de esquemas > categorías > objetos, con carga perezosa
        object_viewer.py   pestaña de un objeto (OBJECT_TABS dice qué pestañas tiene cada tipo)
        code_page.py       pestaña Código: editar, compilar, errores, breakpoints, iniciar depuración
        editors.py         SqlEditor (colores + autocompletado) y CodeEditor (margen, breakpoints)
        completion.py      CompletionProvider: caché de metadatos para sugerencias
        debugger.py        PlsqlDebugger: hilos y cola de órdenes de la depuración
        debug_panel.py     DebugPanel, DebugStartDialog y DebugController (une depurador e interfaz)
        session_keeper.py  SessionKeeper: autoguardado cada AUTOSAVE_MS y hojas de conexiones cerradas
        connect_dialog.py  perfiles, llavero (keyring), tnsnames, reconexión automática al recuperar
        workers.py         TaskRunner y Emitter: llevar resultados de hilos a la interfaz
        style.py           fuente monoespaciada, colores de estado, StatusLabel
        results.py, highlighter.py
    tests/               support.py (FakeConn, FakeCursor, make_session…), conftest.py y pruebas por tema

Detalles útiles:
- Cada conexión usa dos sesiones: la principal (hojas, transacción del usuario, pestaña Datos, código depurado)
  y otra de metadatos (`ConnectionTab.meta_call(fn(cursor), on_ok, on_err)`). La contraseña solo vive en
  memoria dentro de `OracleSession.opener` (lo usa el depurador para abrir una tercera sesión).
- Para agregar una pestaña al visor: un cargador con `@tab_loader` en `db/object_tabs.py` y su nombre en `OBJECT_TABS`.
- `ConnectionTab.current_sheet()` siempre devuelve una `SheetWidget` (nunca un visor).
- Las pruebas reemplazan el depurador real con `PlsqlDebugger.session_factory` y el diálogo con
  `monkeypatch.setattr(main_window, "ConnectDialog", ...)`.

## Reglas que hay que mantener

- Nunca bloquear el hilo de la interfaz con llamadas a Oracle: usar `TaskRunner`/`meta_call` o `run_statement`.
- Nunca tocar widgets desde un hilo de trabajo; emitir una señal y actualizar en el hilo principal.
- `sql/` y `db/` no importan nada de Qt ni de `ui/`.
- Autocommit apagado. Mantener las confirmaciones para UPDATE/DELETE sin WHERE, DROP, TRUNCATE
  y DDL con cambios pendientes.
- Usar bind variables (`:nombre`) en todas las consultas al diccionario; nunca concatenar nombres.
- No guardar contraseñas en archivos.
- Agregar o actualizar pruebas en `tests/` cuando cambie el comportamiento; `ruff check .` sin avisos.
- Si cambia la interfaz, regenerar las capturas del README (`docs/generar_capturas.py`).
- Nunca perder trabajo del usuario: todo estado nuevo que el usuario escriba (hojas, código) debe entrar en
  `ConnectionTab.snapshot()` / `restore()` para que el guardián de sesión lo recupere.
