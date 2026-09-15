#!/usr/bin/env python3
"""
MCP 'ritmo' — el copiloto de jornada (nucleo/ritmo.py) expuesto como
herramientas, para que un agente (Guardian, Analista, Conserje, o uno nuevo
dedicado a esto) pueda planificar el dia, arrancar/cerrar bloques, ofrecer
extensiones con criterio y avisar de vias frias -- sin que el humano tenga
que tocar un boton para todo.

Filosofia (docs/investigacion/estado-del-arte-tdah.md §9): el motor
(nucleo/ritmo.py) decide CUANDO ofrecer o avisar; este MCP es la puerta por
la que un agente puede decidir QUE planificar y CUANDO llamar a esas tools --
p.ej. armar el plan del dia a partir de lo pendiente, o sugerir retomar una
via fria antes de que se enfrie del todo.

Transporte: igual que mcps/foco.py, conexion de control al puente via
WebSocket. RITMO vive UNA vez dentro del proceso del puente (es el ritmo de
una persona, no de un dispositivo) -- este MCP nunca instancia su propio
MotorRitmo, solo habla con el que ya esta corriendo.
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
log = logging.getLogger("mcp-ritmo")

mcp = FastMCP("ritmo")


class Control:
    """Cliente de control contra el puente. Reconecta solo.

    Duplicado deliberado de la clase homonima en mcps/foco.py y
    mcps/dispositivo.py: procesos MCP distintos (cada uno su interprete,
    por diseno de MCP sobre stdio). Ver la nota identica en mcps/foco.py.
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


MODOS_DOC = ("material, oferta, investigacion, construccion, arquitectura, "
             "estudio, multitarea, reunion, descanso")


@mcp.tool()
async def ritmo_estado() -> str:
    """Bloque activo (titulo, modo, minutos restantes, semaforo cyan/ambar/
    rojo), el siguiente bloque, si esta esperando confirmacion para
    arrancarlo (RF-14) y que vias llevan frias.

    Consultar esto antes de sugerir nada: si hay un bloque en 'material' o
    'construccion' activo, no tiene sentido ofrecer empezar otra cosa.
    """
    return _fmt(await _llama("ritmo_estado", {}))


@mcp.tool()
async def ritmo_empieza(titulo: str, modo: str = "multitarea", minutos: int = 0,
                         via: str = "", bloque_id: str = "") -> str:
    f"""Arranca un bloque YA (RF-4: siempre por orden explicita, nunca solo).

    'modo' es uno de: {MODOS_DOC} -- cada uno trae su propia duracion y
    politica de interrupcion (ver docs/investigacion/estado-del-arte-tdah.md
    §3 y §9). 'minutos'=0 usa la duracion por defecto del modo. 'via' agrupa
    el bloque para el seguimiento de RF-16 (p.ej. 'cert-nvidia',
    'charla-npm'); si se omite, usa el modo como via.
    """
    args = {"titulo": titulo, "modo": modo}
    if minutos:
        args["minutos"] = minutos
    if via:
        args["via"] = via
    if bloque_id:
        args["bloque_id"] = bloque_id
    return _fmt(await _llama("ritmo_empieza", args))


@mcp.tool()
async def ritmo_extiende(minutos: int = 0) -> str:
    """Extiende el bloque activo (RF-5) -- usar SOLO cuando el humano acepta
    la oferta que ya hizo el motor ('ofrece_extender' en la vista/aviso), o
    lo pide directo por voz. El motor ya comprobo que el bloque es
    extensible y que no hay una reunion anclada encima; esta tool no vuelve
    a comprobarlo, solo ejecuta la decision humana. 'minutos'=0 usa el
    default (20 min).
    """
    return _fmt(await _llama("ritmo_extiende", {"minutos": minutos} if minutos else {}))


@mcp.tool()
async def ritmo_salta() -> str:
    """Descarta el bloque activo sin marcarlo como vencido -- 'saltate
    esto', 'esto ya no aplica'. Deja el siguiente a la vista, esperando
    confirmacion (RF-14)."""
    return _fmt(await _llama("ritmo_salta", {}))


@mcp.tool()
async def ritmo_cierra() -> str:
    """Cierra el bloque activo ahora mismo, antes de que venza -- 'ya
    termine', o aceptar el cierre en vez de una extension ofrecida. Deja el
    siguiente a la vista, esperando confirmacion (RF-14)."""
    return _fmt(await _llama("ritmo_cierra", {}))


@mcp.tool()
async def ritmo_confirma() -> str:
    """Arranca el bloque que quedo esperando confirmacion tras el anterior
    (RF-14) -- el reloj de ese bloque empieza AHORA, no cuando cerro el
    previo. Usar cuando el humano dice 'dale', 'sigo', o aprieta el boton
    fisico."""
    return _fmt(await _llama("ritmo_confirma", {}))


@mcp.tool()
async def ritmo_plan(bloques_json: str = "") -> str:
    """Lee o reemplaza el plan del dia.

    Sin argumento: devuelve el plan actual. Con 'bloques_json' (una lista
    JSON de objetos con al menos 'titulo' y 'modo', opcionalmente
    'duracion_min'/'via'/'anclado'/'inicio_previsto' en epoch segundos):
    reemplaza el plan completo. NUNCA toca el bloque activo (RF-9):
    reconstruir el plan a mitad de un bloque no lo corta.

    Este es el punto de entrada para que un agente arme el dia solo, p.ej.
    a partir de lo pendiente + lo anclado del calendario (ola agenda) +
    lo dicho por voz -- ver plan_del_dia() en tasks.md Fase 2.
    """
    args = {}
    if bloques_json:
        try:
            bloques = json.loads(bloques_json)
        except json.JSONDecodeError as e:
            return f"bloques_json no es JSON valido: {e}"
        if not isinstance(bloques, list):
            return "bloques_json debe ser una lista de objetos"
        args["bloques"] = bloques
    return _fmt(await _llama("ritmo_plan", args))


@mcp.tool()
async def ritmo_vias() -> str:
    """Vias (temas/proyectos recurrentes) que llevan mas de
    'dias_via_fria' (ajustes.yaml:ritmo, 3 por defecto) sin tocarse (RF-16).

    Pensado para un agente ayudante que, al planificar el dia o en un hueco
    de estudio (RF-10 de SuperPower), sugiera retomar algo antes de que se
    enfrie del todo -- el tipo de seguimiento que a una cabeza con TDAH
    creativa y con muchos frentes abiertos le cuesta sostener sola.
    """
    return _fmt(await _llama("ritmo_vias", {}))


@mcp.tool()
async def ritmo_desglosa(tarea: str, picante: int = 1) -> str:
    """Desglosa una tarea que da paralisis de solo mirarla, en pasos
    concretos y accionables (el 'Magic ToDo' de Goblin Tools, adaptado --
    ver docs/investigacion/estado-del-arte-tdah-2026-actualizacion.md §5.3).

    Usar cuando el humano dice cosas como "no puedo ni empezar con X",
    "se me hace una bola X", o cuando un pendiente lleva dias sin tocarse
    (RF-16, vias frias) y parece que el tamaño es el problema, no el tiempo.

    'picante' (0-2): 0 = pocos pasos grandes (mapa general), 1 = pasos
    medianos (default), 2 = microsteps para cuando ni el primer paso da
    ganas de empezar.
    """
    return _fmt(await _llama("ritmo_desglosa", {"tarea": tarea, "picante": picante}))


if __name__ == "__main__":
    mcp.run()
