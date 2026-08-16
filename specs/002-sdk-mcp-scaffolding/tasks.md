# Tasks: capa común + generador de MCP

- [ ] `sdk_mcp/base.py`: clase base con ciclo de vida + logging + errores.
- [ ] `sdk_mcp/declarativo.py`: soporte para MCP "solo API" sin código.
- [ ] `sdk_mcp/generador.py`: escritura atómica de archivo + ambos YAML.
- [ ] Migrar `mcps/clima.py` a la base común; confirmar con
      `mcps_cli.py --probar` que el comportamiento no cambió.
- [ ] `mcps_cli.py --nuevo`: cuestionario + llamada al generador.
- [ ] Actualizar `MCP.md` con el nuevo flujo recomendado.
- [ ] (Depende de 001) Endpoint en `panel_api` que exponga `crear_mcp`.
