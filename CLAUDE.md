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
Ejecutable: `packaging/MiniSQL.spec` (PyInstaller, un .exe sin consola). `.github/workflows/release.yml` lo compila,
corre `MiniSQL.exe --self-test` (`minisql/selftest.py`) y lo publica en Releases (con `MiniSQL.exe.sha256`) al subir
una etiqueta `vX.Y.Z` igual a `__version__` de `minisql/__init__.py`, única fuente de la versión. La app instalada
se actualiza sola (`minisql/update.py` + `ui/updates.py`, ver docs/contexto/release.md). Si se agrega una dependencia que se importa en tiempo de ejecución o desde código
compilado (como `cryptography` en el modo thin de oracledb), declararla en `hiddenimports` del .spec y cubrirla en selftest.

## Estructura y contexto

Capas de abajo hacia arriba; una capa nunca importa de una superior:
`config.py`, `workspace.py` → `sql/` (texto puro) → `db/` (Oracle, hilos, sin Qt) → `ui/` (Qt).

**Para ahorrar contexto no leas el código a ciegas**: abre `docs/contexto/README.md`, elige el documento
del área (sql, oracle, interfaz, grilla, depuracion, sesiones, pruebas, release, seguridad) y abre solo los
archivos que vas a cambiar. Si un cambio deja desactualizado un documento de `docs/contexto/`, actualízalo
en el mismo cambio.

Agentes del proyecto (`.claude/agents/`), uno por área: `sql-texto`, `oracle-db`, `interfaz-qt`,
`grilla-resultados`, `depurador-plsql`, `guardian-sesion`, `verificador` (pytest/ruff/capturas, resume solo
fallos), `release` y `revisor-seguridad` (solo lectura). Delegar en ellos las tareas de su área y la
verificación mantiene corto el contexto principal.

## Reglas que hay que mantener

- Nunca bloquear el hilo de la interfaz con llamadas a Oracle: usar `TaskRunner`/`meta_call` o `run_statement`.
- Nunca tocar widgets desde un hilo de trabajo; emitir una señal y actualizar en el hilo principal.
- `sql/` y `db/` no importan nada de Qt ni de `ui/`.
- Autocommit apagado. Mantener las confirmaciones para UPDATE/DELETE sin WHERE, DROP, TRUNCATE
  y DDL con cambios pendientes.
- Usar bind variables (`:nombre`) en todas las consultas al diccionario; nunca concatenar nombres.
- No guardar contraseñas en archivos.
- Actualizaciones: solo HTTPS, nunca instalar sin verificar la huella SHA-256 publicada, nunca enviar datos de
  la app en la consulta de versión, y aplicar la instalación solo por el cierre normal de la ventana.
- Agregar o actualizar pruebas en `tests/` cuando cambie el comportamiento; `ruff check .` sin avisos.
- Si cambia la interfaz, regenerar las capturas del README (`docs/generar_capturas.py`).
- Una sola instancia (`app.single_instance_lock`, QLockFile en ~/.minisql/minisql.lock): dos SessionKeeper
  sobre la misma carpeta se pisarían. El ejecutable (.exe) y `python main.py` comparten ~/.minisql.
- Edición de resultados: siempre por ROWID y con bind variables; nunca hacer COMMIT al guardar (queda para el
  usuario). Las ediciones de la grilla sin guardar no van al guardián de sesión: se confirma antes de perderlas
  (ejecutar otra consulta, cerrar la hoja, la conexión o la app).
- Nunca perder trabajo del usuario: todo estado nuevo que el usuario escriba (hojas, código) debe entrar en
  `ConnectionTab.snapshot()` / `restore()` para que el guardián de sesión lo recupere.
