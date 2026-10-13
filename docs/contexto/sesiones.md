# Guardar el trabajo: espacio de trabajo y guardián de sesión

Pruebas: `tests/test_session.py` (WorkspaceStore, guardián), `test_app.py`, `test_sheet.py` (snapshot/restore).

## Formato en disco (workspace.py, `WorkspaceStore`)
    ~/.minisql/sesion/
      indice.json                 conexiones, orden y nombre de hojas, cursor (pequeño); copia en indice.bak
      DEV/Hoja1.sql               texto de cada hoja (solo se reescribe si cambió)
      DEV/objetos/SCOTT.PKG.sql   código sin compilar de un objeto abierto
      DEV (guardadas)/…           hojas de una conexión cerrada, hasta reconectar
- Escritura atómica (`write_text_atomic`, `write_json_atomic`: temporal + reemplazo).
- `safe_name`/`unique_name`: nombres válidos en Windows (sin `<>:"/\|?*`, sin CON/PRN/COM1…).
- Activa: la conexión en `indice.json` → `"current"` (nombre) y la hoja con `"active": true` en su entrada (no por posición: sobrevive a quitar hojas vacías y a reconectar). Formato viejo `"current": índice`, todavía se lee.
- `worth_keeping(state)`: no guardar hojas vacías. `load_session()` migra el formato viejo `sesion.json`.

## Guardián (ui/session_keeper.py, `SessionKeeper`)
- Cada `AUTOSAVE_MS` (3 s) toma `MainWindow` → `ConnectionTab.snapshot()` y guarda.
- `park(state)`: al cerrar una conexión guarda sus hojas en "(guardadas)"; `claim(name)` las devuelve al
  reconectar. `MainWindow.restore_session()` reabre conexiones (connect_dialog reconecta con keyring).
- Una sola instancia (`app.single_instance_lock`): dos guardianes sobre la misma carpeta se pisarían.

## Reglas
- Todo estado nuevo que el usuario escriba (hojas, código) debe entrar en `ConnectionTab.snapshot()` /
  `restore()`. Las ediciones de la grilla sin guardar **no** entran: se confirma antes de perderlas.
- Nunca guardar contraseñas en disco (solo keyring). `.gitignore` ya excluye `sesion/`, `conexiones.json`, `*.ora`.
