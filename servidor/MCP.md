# Cablear MCP — guía rápida

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

## Cómo llegan al asistente

El agente pide al pool la lista de herramientas, se las pasa al modelo en cada
turno, y si el modelo decide usar una, se ejecuta y el resultado vuelve al
modelo para que responda. Eso es `nucleo/agente.py`, y es lo que faltaba antes:
los MCP se conectaban pero nunca se invocaban.

Requisito real: **el modelo debe soportar tool calling**. Por eso Groq va
primero en la cadena — un modelo pequeño local no lo hace, y sin eso las
herramientas no se usan por muchos MCP que actives.
