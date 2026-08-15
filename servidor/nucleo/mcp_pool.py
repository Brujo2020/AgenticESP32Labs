"""
Pool de servidores MCP.

Cada servidor es un subproceso con el que se habla por stdio. Las sesiones
se mantienen vivas con un AsyncExitStack: si se cerraran al salir del
'async with', las herramientas dejarian de responder tras la primera llamada.
"""
import logging, os, sys
from contextlib import AsyncExitStack
from typing import Any

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:
    print("Falta el paquete MCP:  pip install mcp")
    sys.exit(1)

log = logging.getLogger("mcp")


def _expande(v):
    if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
        return os.getenv(v[2:-1], "")
    return v


class MCPPool:
    def __init__(self, config_path: str = "config.yaml"):
        from .config import Config
        self.config = Config(config_path)
        self.stack = AsyncExitStack()
        self.sesiones: dict[str, ClientSession] = {}
        self.herramientas: dict[str, dict] = {}     # nombre -> {servidor, esquema}

    async def connect_all(self):
        servidores = self.config.get_mcp_servers()
        for nombre in self.config.get_enabled_tools():
            cfg = servidores.get(nombre)
            if not cfg:
                log.warning("'%s' esta en tools_to_enable pero no en mcp_servers", nombre)
                continue
            try:
                await self._conecta(nombre, cfg)
            except Exception as e:
                # Que falle un MCP no puede tumbar al resto
                log.error("MCP '%s' no arranco: %s", nombre, e)
        log.info("MCP activos: %s | %d herramientas",
                 list(self.sesiones), len(self.herramientas))

    async def _conecta(self, nombre: str, cfg: dict):
        entorno = {k: _expande(v) for k, v in (cfg.get("env") or {}).items()}
        comando = cfg["command"][0]
        # "python3"/"python" en config.yaml es una referencia generica: se
        # resuelve al MISMO interprete que esta corriendo este proceso
        # (sys.executable), no al que encuentre el PATH del subproceso. Sin
        # esto, un MCP propio (mcps/clima.py, sistema.py, mac.py...) puede
        # arrancar con un python3 distinto al del venv activo -> le falta el
        # paquete 'mcp' o trae una version vieja sin mcp.server.fastmcp, y
        # el fallo ("ModuleNotFoundError", "Connection closed") no deja ver
        # que la causa es simplemente "el subproceso no es el venv correcto".
        if comando in ("python3", "python"):
            comando = sys.executable
        params = StdioServerParameters(
            command=comando,
            args=cfg["command"][1:],
            env={**os.environ, **entorno},
        )
        lectura, escritura = await self.stack.enter_async_context(stdio_client(params))
        sesion = await self.stack.enter_async_context(ClientSession(lectura, escritura))
        await sesion.initialize()
        self.sesiones[nombre] = sesion

        for t in (await sesion.list_tools()).tools:
            self.herramientas[t.name] = {
                "servidor": nombre,
                "esquema": {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": t.inputSchema or {"type": "object", "properties": {}},
                    },
                },
            }
        log.info("MCP '%s' listo", nombre)

    def esquemas(self) -> list[dict]:
        """Herramientas en el formato que espera la API de OpenAI."""
        return [h["esquema"] for h in self.herramientas.values()]

    def list_available_tools(self) -> list[str]:
        """Nombres de las herramientas conectadas ahora mismo (usado por consola.py)."""
        return list(self.herramientas)

    async def invoca(self, nombre: str, argumentos: dict) -> str:
        h = self.herramientas.get(nombre)
        if not h:
            return f"La herramienta '{nombre}' no existe"
        try:
            r = await self.sesiones[h["servidor"]].call_tool(nombre, argumentos)
            partes = [c.text for c in r.content if hasattr(c, "text")]
            return "\n".join(partes) or "(sin salida)"
        except Exception as e:
            log.error("fallo la herramienta '%s': %s", nombre, e)
            return f"Error al ejecutar {nombre}: {e}"

    async def cerrar(self):
        await self.stack.aclose()
