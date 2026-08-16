# Tasks: capa común + generador de MCP

- [x] `sdk_mcp/base.py`: `MCPBase`, envuelve FastMCP con log y errores
      homogeneos por tool.
- [x] `sdk_mcp/declarativo.py`: MCP solo-API via entrada de catalogo.
      **Limitacion conocida documentada en el propio archivo**: el esquema de
      parametros que ve el modelo es generico (no infiere tipos desde
      `parametros:` en el YAML todavia) — arreglo pendiente con
      `inspect.Signature`.
- [x] `sdk_mcp/generador.py`: `crear_mcp()` — escribe `mcps/<nombre>.py` (o
      la entrada declarativa) y ambos YAML en una sola operacion; nunca deja
      `tools_to_enable`/`mcp_servers` desincronizados.
- [x] Migrar `mcps/clima.py` a la base comun (mismas tools, mismo
      comportamiento). **Falta correr `mcps_cli.py --probar` en un entorno
      con `pip install -r requirements.txt` hecho** — no se pudo ejecutar
      desde aqui (sin el venv del proyecto), solo se verifico sintaxis con
      `py_compile`.
- [ ] `mcps_cli.py --nuevo`: cuestionario + llamada al generador.
      **Pendiente** — el generador ya existe y esta cableado desde el panel
      web (`POST /api/mcp/nuevo`), falta el atajo de CLI equivalente.
- [ ] Actualizar `MCP.md` con el nuevo flujo recomendado.
- [x] Endpoint en `panel_api` que expone `crear_mcp` (`POST /api/mcp/nuevo`).
