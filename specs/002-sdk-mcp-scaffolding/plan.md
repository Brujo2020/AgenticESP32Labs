# Plan: capa común + generador de MCP

## Diseño

- Nuevo paquete `servidor/sdk_mcp/` con:
  - `base.py`: clase `MCPBase` (o mixin) — ciclo de vida, logging homogéneo,
    formato de error uniforme (hoy cada `mcps/*.py` maneja errores a su modo).
  - `declarativo.py`: intérprete de una entrada de catálogo tipo
    `backend: rest` / `backend: openai-compatible` que genera tools sin
    código, mismo principio que ya usa `PROVEEDORES.md` para LLM.
  - `generador.py`: función pura `crear_mcp(nombre, categoria, tools, ...)`
    que escribe `mcps/<nombre>.py` (si no es declarativo) y edita
    `mcp_catalogo.yaml` + `config.yaml` (`mcp_servers` y, si se pide,
    `tools_to_enable`) en una sola operación — sin dejar las dos fuentes
    desincronizadas (el bug ya documentado).
- CLI: `mcps_cli.py --nuevo` llama a `generador.crear_mcp(...)` tras un
  cuestionario corto.
- El panel web (spec 001) llamará a la misma `generador.crear_mcp` vía un
  endpoint del `panel_api`, no reimplementa la lógica.

## Migración de prueba

- Reescribir `mcps/clima.py` (el más simple, sin estado local) sobre
  `MCPBase` como prueba de que la capa común no cambia el comportamiento
  externo (mismas tools, mismas respuestas).

## Riesgos
- Cambiar la forma de `mcps/*.py` puede romper `mcps_cli.py --probar` si no
  se mantiene compatible la interfaz; se mitiga migrando uno solo primero y
  corriendo las pruebas existentes antes de tocar el resto.
