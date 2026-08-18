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
        # Un AsyncExitStack POR servidor, no uno compartido: para desconectar
        # un solo MCP en caliente (activar()/desactivar(), hot-reload de
        # 001-panel-administracion-mcp Fase 0) hace falta poder cerrar su
        # stdio_client+ClientSession sin tocar los de los demas. Un stack
        # unico solo se puede cerrar entero (cerrar() lo sigue haciendo asi,
        # de golpe, al apagar el proceso).
        self.stacks: dict[str, AsyncExitStack] = {}
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
        stack = AsyncExitStack()
        try:
            lectura, escritura = await stack.enter_async_context(stdio_client(params))
            sesion = await stack.enter_async_context(ClientSession(lectura, escritura))
            await sesion.initialize()
        except Exception:
            await stack.aclose()
            raise
        self.stacks[nombre] = stack
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

    # ------------------------------------------------------------
    #  Hot-reload (Fase 0 de 001-panel-administracion-mcp/tasks.md): activar
    #  o desactivar UN MCP sin tocar los demas ni reiniciar este proceso.
    # ------------------------------------------------------------
    async def activar(self, nombre: str) -> bool:
        """Conecta un MCP concreto. Devuelve False si ya estaba conectado."""
        if nombre in self.sesiones:
            return False
        cfg = self.config.get_mcp_servers().get(nombre)
        if not cfg:
            raise ValueError(f"'{nombre}' no esta en mcp_servers de config.yaml")
        await self._conecta(nombre, cfg)
        return True

    async def desactivar(self, nombre: str) -> bool:
        """Desconecta un MCP concreto. Devuelve False si no estaba conectado."""
        stack = self.stacks.pop(nombre, None)
        if stack is None:
            return False
        self.sesiones.pop(nombre, None)
        for tool_nombre in [t for t, h in self.herramientas.items() if h["servidor"] == nombre]:
            del self.herramientas[tool_nombre]
        await stack.aclose()
        log.info("MCP '%s' desconectado", nombre)
        return True

    async def sincroniza(self, config=None) -> dict:
        """Ajusta las conexiones vivas a lo que dice config.yaml AHORA MISMO,
        sin reiniciar el proceso: conecta lo que se activo desde el panel,
        desconecta lo que se desactivo. Se llama tras detectar que config.yaml
        cambio (ver Config.recarga_si_cambio en websocket_bridge.py).

        Un MCP que falla al conectar no rompe la sincronizacion del resto
        (mismo criterio que connect_all): queda registrado en el log y se
        reintenta en la siguiente sincronizacion.
        """
        if config is not None:
            self.config = config
        servidores = self.config.get_mcp_servers()
        deseados = set(self.config.get_enabled_tools()) & set(servidores)
        conectados = set(self.sesiones)

        activados, desactivados, fallidos = [], [], []
        for nombre in deseados - conectados:
            try:
                await self._conecta(nombre, servidores[nombre])
                activados.append(nombre)
            except Exception as e:
                log.error("hot-reload: MCP '%s' no arranco: %s", nombre, e)
                fallidos.append(nombre)
        for nombre in conectados - deseados:
            await self.desactivar(nombre)
            desactivados.append(nombre)
        return {"activados": activados, "desactivados": desactivados, "fallidos": fallidos}

    def esquemas(self, servidores: list[str] | None = None) -> list[dict]:
        """Herramientas en el formato que espera la API de OpenAI.

        'servidores' acota a los MCP indicados: es como un perfil de agente
        del panel web limita lo que ese agente puede tocar. None = todas.
        Un servidor que no exista se ignora en silencio (no rompe el chat).
        """
        if servidores is None:
            return [h["esquema"] for h in self.herramientas.values()]
        permitidos = set(servidores)
        return [h["esquema"] for h in self.herramientas.values()
                if h["servidor"] in permitidos]

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
        for nombre in list(self.stacks):
            try:
                await self.stacks.pop(nombre).aclose()
            except Exception as e:
                log.warning("fallo cerrando MCP '%s': %s", nombre, e)
        self.sesiones.clear()
        self.herramientas.clear()
