# Spec: capa común + generador para crear MCP nuevos rápido

**Estado**: borrador para revisión — no implementar hasta aprobar.
**Habilita a**: `001-panel-administracion-mcp` (botón "MCP nuevo") y a
`003-transportes-y-mac-mcp` (los nodos remotos son MCP creados con esto).

## Por qué

Punto de mejora #1 y #7 de `../MEJORAS.md`: cada MCP propio (`mcps/clima.py`,
`mcps/mac.py`, `mcps/sistema.py`, `mcps/dispositivo.py`, `mcps/vigia.py`) repite
el mismo boilerplate y no hay plantilla ni generador. Añadir uno hoy es
copiar el archivo que más se parezca y adivinar qué tocar en dos YAML
distintos. El objetivo del proyecto pide explícitamente que crear MCP nuevos
sea "ordenado, escalable, rápido de crear".

## Historias de usuario

1. **Como desarrollador**, corro un comando (`mcps_cli.py --nuevo` o
   equivalente) y contesto 3-4 preguntas (nombre, categoría, qué tools
   expone) y obtengo un `mcps/<nombre>.py` funcional con el boilerplate ya
   resuelto y las dos entradas de YAML (`mcp_servers` + catálogo) escritas
   solas.
2. **Como desarrollador**, mi MCP nuevo hereda automáticamente: manejo de
   errores homogéneo, logging con el mismo formato que los demás, y aparece
   en `mcps_cli.py --listar` sin código extra.
3. **Como administrador** (desde el panel de la spec 001), lleno un
   formulario en vez de correr un comando y pasa lo mismo.
4. **Como desarrollador**, un MCP que solo envuelve una API externa
   "openai-compatible" o REST simple no debería requerir escribir clase
   Python — una entrada declarativa en el catálogo debería bastar (mismo
   principio que ya existe para proveedores LLM en `PROVEEDORES.md`).

## Alcance

### Dentro
- Una clase base / mixin en `nucleo/` (o nuevo paquete `sdk_mcp/`) que
  encapsule: ciclo de vida (arrancar, parar, healthcheck), registro de tools,
  formato de error, formato de log — para que un MCP nuevo sea "defino mis
  tools, heredo el resto".
- Generador (CLI, y luego reusado por el panel web) que crea el archivo y
  edita las dos entradas de YAML necesarias, sin dejarlas descoordinadas
  (elimina la clase de bug ya documentada: "está en tools_to_enable pero no
  en mcp_servers").
- Camino declarativo (sin código) para MCP que solo llaman una API REST u
  OpenAI-compatible.
- Migrar (reescribir con la nueva base, mismo comportamiento externo) al
  menos un MCP existente como prueba de que la capa común no rompe nada.

### Fuera
- Un marketplace público de MCP de terceros.
- Sandboxing/seguridad por proceso más allá de lo que ya da "cada tool en su
  proceso" (eso queda para una spec de seguridad si hace falta).

## Criterios de aceptación

- [ ] Crear un MCP nuevo de "solo API REST" no requiere escribir una clase
      Python, solo una entrada declarativa.
- [ ] Crear un MCP nuevo "con lógica" (como `mac.py`) usando la base común
      toma menos líneas que el equivalente actual sin ella.
- [ ] El generador nunca deja el catálogo y `tools_to_enable`/`mcp_servers`
      desincronizados: son una sola operación atómica.
- [ ] Un MCP existente migrado a la base común pasa las mismas pruebas de
      `mcps_cli.py --probar` que antes.
- [ ] La documentación (`MCP.md`) queda actualizada con "así se crea un MCP
      nuevo hoy", reemplazando el flujo manual como método recomendado.

## Preguntas abiertas
- ¿El generador vive solo en Python/CLI, o también como función invocable
  por HTTP para que el panel web lo use directo (mismo código, dos entradas)?
