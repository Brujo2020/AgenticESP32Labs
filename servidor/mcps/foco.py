#!/usr/bin/env python3
"""
MCP 'foco' — SuperPower expuesto como herramientas.

SuperPower (nucleo/foco.py) es el corazon del producto: infiere si estoy en
HyperFocus, disperso, en descanso o ausente, y decide si un aviso pasa ahora
o se guarda para el proximo hueco. Este MCP es la puerta por la que un
humano -- o el propio agente -- interactua con esa maquina de estados:
consultar en que estado esta, pedir un descanso deliberado (que entrega lo
guardado), reanudar (que recupera las migas de pan) y dejar una nota de
donde se iba a seguir.

Transporte: igual que mcps/dispositivo.py, conexion de control al puente
via WebSocket. El MOTOR vive UNA vez dentro del proceso del puente (es el
estado de una persona, no de un dispositivo) -- este MCP nunca instancia su
propio MotorFoco, solo habla con el que ya esta corriendo.
"""
import asyncio, json, os, sys, logging

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("Falta el paquete MCP:  pip install mcp", file=sys.stderr)
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("Falta websockets:  pip install websockets", file=sys.stderr)
    sys.exit(1)

PUENTE = os.getenv("HUD_BRIDGE", "ws://127.0.0.1:8765")
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
log = logging.getLogger("mcp-foco")

mcp = FastMCP("foco")


class Control:
    """Cliente de control contra el puente. Reconecta solo.

    Duplicado deliberado de la clase homonima en mcps/dispositivo.py: son
    dos procesos MCP distintos (cada uno su propio interprete, por diseno
    de MCP sobre stdio) y compartir el objeto no es posible sin un tercer
    servicio. La duplicacion es pequeña (40 lineas) y estable; no vale la
    complejidad de extraerla a un paquete compartido por un unico caso.
    """

    def __init__(self):
        self.ws = None
        self.rid = 0
        self.esperando: dict[int, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    async def _conecta(self):
        if self.ws is not None:
            return
        self.ws = await websockets.connect(PUENTE, max_size=None)
        await self.ws.send(json.dumps({"t": "hola", "rol": "control"}))
        asyncio.create_task(self._lee())

    async def _lee(self):
        try:
            async for msg in self.ws:
                if isinstance(msg, bytes):
                    continue
                d = json.loads(msg)
                if d.get("t") == "res":
                    fut = self.esperando.pop(d.get("rid"), None)
                    if fut and not fut.done():
                        fut.set_result(d.get("v"))
        except websockets.ConnectionClosed:
            pass
        finally:
            self.ws = None

    async def llama(self, fn: str, args: dict, timeout: float = 15):
        async with self._lock:
            await self._conecta()
            self.rid += 1
            rid = self.rid
        fut = asyncio.get_running_loop().create_future()
        self.esperando[rid] = fut
        await self.ws.send(json.dumps({"t": "cmd", "rid": rid, "fn": fn, "args": args}))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self.esperando.pop(rid, None)
            return {"error": f"el puente no respondio a {fn} en {timeout}s"}


CTL = Control()


async def _llama(fn, args, timeout=15):
    try:
        r = await CTL.llama(fn, args, timeout)
    except Exception as e:
        return f"No hay puente disponible: {e}"
    if isinstance(r, dict) and "error" in r:
        return f"Error: {r['error']}"
    return r


def _fmt(r):
    return json.dumps(r, ensure_ascii=False, indent=2) if isinstance(r, dict) else r


@mcp.tool()
async def foco_estado() -> str:
    """Estado actual de SuperPower: libre / hyperfocus (foco) / disperso /
    descanso / ausente, minutos en ese estado, la app sostenida, cuantos
    avisos hay guardados en la bandeja y las migas de pan de la ultima vez
    que se salio de HyperFocus (para saber "donde iba").

    Consultar esto ANTES de mandar un aviso ambiental es lo que le da al
    agente la posibilidad de callarse: si el estado es 'foco', un titular de
    noticias puede esperar al proximo hueco.
    """
    return _fmt(await _llama("foco_estado", {}))


@mcp.tool()
async def foco_descanso(minutos: int = 0) -> str:
    """Marca un descanso DELIBERADO (RF-8) y entrega de golpe todo lo que
    SuperPower tenia guardado mientras protegia el HyperFocus -- avisos que
    no se perdieron, solo esperaron su momento (RF-5).

    'minutos' opcional: duracion del descanso. 0 usa el valor por defecto
    del motor (10 min). Al vencer, SuperPower vuelve solo a 'libre' y abre
    un hueco de estudio (RF-10).
    """
    return _fmt(await _llama("foco_descanso", {"minutos": minutos}))


@mcp.tool()
async def foco_reanuda() -> str:
    """Sale del descanso YA (sin esperar a que venza) y devuelve las migas
    de pan: en que app y con que nota se quedo la ultima sesion de
    HyperFocus (RF-7). Es la respuesta a "¿en que estaba?".
    """
    return _fmt(await _llama("foco_reanuda", {}))


@mcp.tool()
async def foco_anota(nota: str) -> str:
    """Deja una miga de pan explicita antes de cortar -- 'iba a probar el
    endpoint del clima', 'faltaba el caso de la bola desconectada'. Se
    devuelve la proxima vez que se llame a foco_reanuda() o foco_estado().

    Con TDAH lo caro no es la interrupcion: es volver. Esta tool es la
    version barata de ese problema.
    """
    return _fmt(await _llama("foco_anota", {"nota": nota}))


if __name__ == "__main__":
    mcp.run()
