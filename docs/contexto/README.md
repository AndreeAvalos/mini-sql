# Contexto por área

Documentación corta para trabajar en MiniSQL sin leer todo el código. Cada archivo dice qué módulos tocar,
las funciones clave, las reglas del área y qué pruebas la cubren. **Lee solo el del área de tu tarea**;
abre el código únicamente de los archivos que vayas a cambiar.

| Tarea | Lee | Agente |
|---|---|---|
| Texto SQL: separar sentencias, formato, autocompletado, alias, buscar/reemplazar | [sql.md](sql.md) | `sql-texto` |
| Oracle: sesiones, consultas al diccionario, ejecutar, commit/rollback, DBMS_OUTPUT | [oracle.md](oracle.md) | `oracle-db` |
| Interfaz Qt: ventana, conexiones, hojas, explorador, visor de objetos, editores | [interfaz.md](interfaz.md) | `interfaz-qt` |
| Grilla de resultados: edición, filtro, orden, Guardar/Cancelar | [grilla.md](grilla.md) | `grilla-resultados` |
| Código PL/SQL: compilar, errores, depuración (DBMS_DEBUG) | [depuracion.md](depuracion.md) | `depurador-plsql` |
| Guardar el trabajo: hojas en disco, guardián de sesión, recuperación | [sesiones.md](sesiones.md) | `guardian-sesion` |
| Pruebas, ruff, capturas del README | [pruebas.md](pruebas.md) | `verificador` |
| Ejecutable, GitHub Actions, versiones y releases | [release.md](release.md) | `release` |
| Revisión de seguridad (DML peligroso, binds, credenciales) | [seguridad.md](seguridad.md) | `revisor-seguridad` |

Mapa rápido de capas (una capa nunca importa de una superior):

    config.py, workspace.py   →  sql/ (texto puro)  →  db/ (Oracle, hilos, sin Qt)  →  ui/ (Qt)

Las reglas generales están en `CLAUDE.md`. Si cambias algo que estos documentos describen
(una función clave, un archivo nuevo, una regla), actualiza el documento del área en el mismo cambio.
