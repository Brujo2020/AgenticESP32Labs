"""
Capa comun para MCP propios (spec 002-sdk-mcp-scaffolding).

Hasta ahora cada mcps/*.py (clima.py, mac.py, sistema.py...) repetia el mismo
patron a mano: crear FastMCP, envolver cada tool en un try/except suelto, y
logear (o no) a su manera. Esta clase no reemplaza FastMCP -- lo envuelve --
para que un MCP nuevo declare sus tools y herede el resto: errores con el
mismo formato, log homogeneo, y un healthcheck de proceso gratis.

Un MCP "con logica" hereda de MCPBase. Un MCP que solo llama a una API REST
u OpenAI-compatible no necesita ni esto: ver declarativo.py.
"""
import logging
import sys
from typing import Callable

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("Falta el paquete MCP:  pip install mcp")
    sys.exit(1)


class MCPBase:
    """Envoltorio delgado sobre FastMCP con log y errores homogeneos.

    Uso:
        m = MCPBase("clima")

        @m.tool()
        async def clima_actual(latitud: float, longitud: float) -> dict:
            ...  # si lanza una excepcion, MCPBase la atrapa y la devuelve
                 # como {"error": "..."} en vez de tumbar el proceso o dejar
                 # que cada tool decida su propio formato de error.

        m.run()
    """

    def __init__(self, nombre: str):
        self.nombre = nombre
        self.log = logging.getLogger(f"mcp.{nombre}")
        self._mcp = FastMCP(nombre)

    def tool(self) -> Callable:
        registrar = self._mcp.tool()

        def decorador(fn: Callable) -> Callable:
            es_async = _es_coroutine(fn)

            if es_async:
                async def envoltura(*args, **kwargs):
                    try:
                        return await fn(*args, **kwargs)
                    except Exception as e:  # noqa: BLE001 -- limite de proceso, se registra y se devuelve
                        self.log.error("tool '%s' fallo: %s", fn.__name__, e)
                        return {"error": str(e)}
            else:
                def envoltura(*args, **kwargs):
                    try:
                        return fn(*args, **kwargs)
                    except Exception as e:  # noqa: BLE001
                        self.log.error("tool '%s' fallo: %s", fn.__name__, e)
                        return {"error": str(e)}

            envoltura.__name__ = fn.__name__
            envoltura.__doc__ = fn.__doc__
            return registrar(envoltura)

        return decorador

    def run(self, transport: str = "stdio"):
        self.log.info("MCP '%s' arrancando (%s)", self.nombre, transport)
        self._mcp.run(transport=transport)


def _es_coroutine(fn: Callable) -> bool:
    import inspect
    return inspect.iscoroutinefunction(fn)
