# Seguridad: datos y credenciales

## Proteger los datos del usuario
- La app **nunca** ejecuta DML que el usuario no escribió, salvo la grilla (UPDATE/DELETE/INSERT por ROWID,
  una fila por operación, con savepoint).
- Confirmaciones obligatorias (sheet.py `confirm`, `sql/text.changes_without_where`): UPDATE/DELETE sin
  WHERE propio, DROP, TRUNCATE, DDL con cambios pendientes, borrar filas en la grilla.
- Autocommit apagado; nunca COMMIT implícito. Rollback al cerrar si el usuario lo elige.
- La sentencia ejecutada es exactamente la seleccionada o la del cursor (separada por líneas en blanco):
  revisar `statement_range` ante cualquier cambio para que no se mezclen sentencias.

## SQL
- Bind variables en todas las consultas al diccionario y en la grilla. Identificadores en PL/SQL generado
  citados con `q()`. Nada de f-strings con valores del usuario en SQL.

## Credenciales
- Contraseña: solo en memoria (`OracleSession.opener`) y en el llavero de Windows (`keyring`,
  servicio `minisql-oracle`). Nunca en `conexiones.json`, sesiones, logs, mensajes de error ni pruebas.
- `.gitignore` excluye `*.ora`, `tnsnames.ora`, `conexiones.json`, `sesion/`. El repo es público:
  revisar que capturas, pruebas y ejemplos usen datos inventados (DESARROLLO, SCOTT, VENTAS).

## Revisión rápida
    git grep -niE "password|passwd|pwd|secret|token" -- ':!docs/contexto'
    git grep -nE "f\"[^\"]*(select|update|delete|insert)[^\"]*\{" -- minisql
