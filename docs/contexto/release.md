# Ejecutable, CI, releases y actualización automática

## Versión
- **Única fuente:** `__version__` en `minisql/__init__.py`. pyproject.toml la lee (`dynamic`), el .spec la pone en
  Propiedades del .exe, release.yml exige que la etiqueta sea igual y el actualizador compara con ella.
- Versiones de prueba: `0.3.0-beta.1` (etiqueta `v0.3.0-beta.1` → release marcado como prerelease).

## GitHub Actions
- `.github/workflows/tests.yml`: ruff + pytest en Windows y Linux, Python 3.10 y 3.13.
- `.github/workflows/release.yml` (también `workflow_call` desde version.yml): al subir `vX.Y.Z` compila con PyInstaller, corre `MiniSQL.exe --self-test`,
  calcula `MiniSQL.exe.sha256` y publica ambos en Releases (prerelease si la etiqueta tiene `-`).

## Versión automática con [CI]
- `.github/workflows/version.yml`: push a main con `[CI]`, `[CI minor]` o `[CI major]` en algún commit →
  `packaging/bump_version.py` decide el número (si `__version__` no tiene etiqueta, ese mismo; si no, sube; gana
  la subida más grande del push) → el bot hace el commit «Versión X.Y.Z» → llama a release.yml (`workflow_call`
  con `tag` y `ref`). El Release crea la etiqueta al final: si algo falla no queda etiqueta y el siguiente [CI]
  reintenta el mismo número. Los commits/etiquetas del bot (GITHUB_TOKEN) no disparan workflows: sin ciclos.
- Pruebas: `tests/test_bump_version.py`. Si se protege main, el bot necesita permiso para hacer push.

## Ejecutable
- `packaging/MiniSQL.spec`: un solo .exe sin consola. `.gitignore` ignora `*.spec` salvo este.
- Dependencias cargadas en tiempo de ejecución (p. ej. `cryptography` para el modo thin de oracledb,
  backends de keyring) van en `hiddenimports` **y** se verifican en `minisql/selftest.py`.
- `selftest.py` también revisa que la versión sea válida y que se carguen los certificados de Windows (HTTPS del
  actualizador). Crea la ventana con un `UpdateManager` inactivo para no tocar el config.json del usuario.

## Actualización automática
- `minisql/update.py` (sin Qt): `latest_release` (API de GitHub, ignora borradores, prereleases si no se piden y
  releases sin MiniSQL.exe), `should_check` (1 vez al día), `download` (a `~/.minisql/actualizaciones`, `.part`
  hasta verificar SHA-256 del `.sha256` publicado o el `digest` de GitHub; sin huella no instala), `self_test` del
  .exe nuevo, `install` (renombra el actual a `MiniSQL.old.exe` y mueve el nuevo; si falla, deshace),
  `rollback` (intercambia), `clean_downloads`, `relaunch`.
- `minisql/ui/updates.py`: `UpdateManager` (aviso `UpdateBanner`, menú ⋯ con buscar/auto/prueba/volver atrás/acerca
  de, descarga con `QProgressDialog`). Solo revisa sola en el .exe (`frozen`); desde el código no hace red ni
  escribe config.json. Ajustes en config.json → `"updates"`: auto, prerelease, last_check, skip, previous_version,
  last_run_version.
- Instalar = `restart_now()` → `window.close()` (todas las confirmaciones de siempre) → `closeEvent` guarda la
  sesión → `updates.on_exit()` instala → `app.main` suelta el candado de instancia única y `relaunch`.
- Pruebas: `tests/test_update.py` (urlopen simulado, `tmp_path`, fábrica `updates=` de MainWindow).

## Publicar una versión
1. Subir `__version__`. 2. Commit. 3. `git tag vX.Y.Z` y `git push origin vX.Y.Z`. Repositorio: `AndreeAvalos/mini-sql`.

## Reglas
- Commits y push solo cuando el usuario lo pida.
- Nada de datos de conexión, tnsnames ni sesiones en el repositorio.
- Actualizador: solo HTTPS, nunca ejecutar sin verificar SHA-256, no enviar datos, instalar solo al cerrar.
