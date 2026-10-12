# Ejecutable, CI y releases

## GitHub Actions
- `.github/workflows/tests.yml`: ruff + pytest en Windows y Linux, Python 3.10 y 3.13. En Linux, keyring
  necesita un backend: las pruebas no deben depender del llavero real.
- `.github/workflows/release.yml`: al subir una etiqueta `vX.Y.Z` compila con PyInstaller, corre
  `MiniSQL.exe --self-test` y publica el .exe en Releases. **La etiqueta debe ser igual a `version` de
  pyproject.toml** (si no, el workflow falla a propósito).

## Ejecutable
- `packaging/MiniSQL.spec`: un solo .exe sin consola. `.gitignore` ignora `*.spec` salvo este.
- Dependencias cargadas en tiempo de ejecución (p. ej. `cryptography` para el modo thin de oracledb,
  backends de keyring) van en `hiddenimports` **y** se verifican en `minisql/selftest.py`.
- Dependencias: `pyproject.toml` es la fuente; `requirements*.txt` las repiten.

## Publicar una versión
1. Subir `version` en pyproject.toml (y lo que muestre la app). 2. Commit. 3. `git tag vX.Y.Z` y
`git push origin vX.Y.Z`. Repositorio: `AndreeAvalos/mini-sql` (público).

## Reglas
- Commits y push solo cuando el usuario lo pida.
- Nada de datos de conexión, tnsnames ni sesiones en el repositorio.
