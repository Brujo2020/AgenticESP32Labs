# Cablear MCP — guía rápida

## Desde el panel web (recomendado)

Si el servidor tiene el panel corriendo (`servidor/panel_api.py`, ver
`servidor/deploy/README.md`), es la forma mas rapida: entra a
`http://<host-del-servidor>:8766/` con el `PANEL_TOKEN`, y desde ahi:

- Activas/desactivas cualquier MCP del catalogo con un switch.
- Creas un MCP nuevo con un formulario -- llama al mismo generador que el
  atajo de CLI de mas abajo, asi que el resultado es identico.
- Ves de un vistazo cuales estan activos y cuales fallaron al arrancar.

No reemplaza `mcps_cli.py`/`consola.py` -- los reusa por debajo, asi que
todo lo de esta guia sigue siendo valido si prefieres terminal.

## Desde VSCode

Abre la carpeta `~/servidor` y usa **`Cmd+Shift+P` → "Tasks: Run Task"**:

| Tarea | Para qué |
|---|---|
| **MCP: gestionar** | Menú interactivo: ves todos, activas por número |
| **MCP: probar los activos** | Los arranca de verdad y lista sus herramientas |
| **Puente de voz: arrancar** | El servidor que habla con el ESP32 |
| **Agente: consola de pruebas** | Probar por texto sin usar la placa |
| **Instalar dependencias** | `pip install -r requirements.txt` |

`Cmd+Shift+B` lanza directamente el gestor de MCP.

## Desde terminal

```bash
python3 mcps_cli.py            # menú
python3 mcps_cli.py --listar   # estado de todos
python3 mcps_cli.py --probar   # arranca los activos y lista herramientas
python3 mcps_cli.py --nuevo    # cuestionario para crear un MCP nuevo
```

El gestor te dice **por qué** algo no está listo: si falta el binario (`npx`, `uvx`)
o si falta una variable de entorno, en lugar de fallar en silencio al arrancar.

## Lo que hace falta según el MCP

Muchos MCP son paquetes que se descargan al vuelo, así que basta tener el
lanzador:

```bash
brew install node        # para los que usan npx
brew install uv          # para los que usan uvx
```

Los que necesitan credenciales las leen del entorno. Ponlas en tu `~/.zshrc`
para no repetirlo:

```bash
export GROQ_API_KEY=gsk_...          # LLM y transcripción, gratis
export BRAVE_API_KEY=...             # búsqueda web
export GITHUB_TOKEN=ghp_...          # repos, issues, PRs
```

## Añadir uno que no esté

### Que ya existe en algun lado (npx, uvx...)

Entrada nueva en `mcp_catalogo.yaml`:

```yaml
mi-mcp:
  categoria: local
  descripcion: "Qué hace, en una línea"
  command: ["npx", "-y", "@quien/paquete"]
  env: { MI_CLAVE: "${MI_CLAVE}" }
  requisitos: "Lo que hay que tener antes"
```

Aparece en el menú automáticamente. No se toca ni el agente ni el firmware.

### Uno propio, desde cero

En vez de escribir el YAML a mano en dos archivos (y arriesgarte al bug
clasico de "esta en tools_to_enable pero no en mcp_servers"), usa el
generador (spec `002-sdk-mcp-scaffolding`):

```bash
python3 mcps_cli.py --nuevo
```

Te pregunta nombre, descripcion y categoria, y crea `mcps/<nombre>.py` con
la plantilla lista (usa `sdk_mcp.MCPBase`, que da logging y manejo de
errores homogeneo por tool) mas las dos entradas de YAML en una sola
operacion. Reemplaza la tool de ejemplo por la logica real y listo.

Si tu MCP solo envuelve una API REST u OpenAI-compatible (sin logica propia),
no hace falta ni escribir la clase Python -- ver `sdk_mcp/declarativo.py` y
la seccion `declarativo:` del catalogo.

## Cómo llegan al asistente

El agente pide al pool la lista de herramientas, se las pasa al modelo en cada
turno, y si el modelo decide usar una, se ejecuta y el resultado vuelve al
modelo para que responda. Eso es `nucleo/agente.py`, y es lo que faltaba antes:
los MCP se conectaban pero nunca se invocaban.

Requisito real: **el modelo debe soportar tool calling**. Por eso Groq va
primero en la cadena — un modelo pequeño local no lo hace, y sin eso las
herramientas no se usan por muchos MCP que actives.
