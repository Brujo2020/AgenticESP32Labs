#!/usr/bin/env python3
"""
MCP 'dispositivo' — el ESP32 expuesto como herramientas.

Aqui se invierte la relacion del proyecto: hasta ahora el ESP32 era cliente
del agente y solo pintaba lo que le mandaban. Con este servidor el dispositivo
pasa a ser una *capacidad* que cualquier cliente MCP puede usar: el agente del
puente, Claude Desktop, Claude Code o cualquier otro.

Consecuencia practica: el HUD se convierte en el canal de aprobacion fisica de
todo tu stack agentico. Un agente a punto de hacer algo irreversible te lo
pregunta en la pantalla que tienes en el escritorio, no en una ventana tapada.

Transporte: se conecta al puente como cliente WebSocket con rol 'control'.
Se reutiliza el socket que ya existe en vez de abrir un segundo canal.
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
log = logging.getLogger("mcp-dispositivo")

mcp = FastMCP("dispositivo")


class Control:
    """Cliente de control contra el puente. Reconecta solo."""

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
                    continue                      # audio, no nos incumbe
                d = json.loads(msg)
                if d.get("t") == "res":
                    fut = self.esperando.pop(d.get("rid"), None)
                    if fut and not fut.done():
                        fut.set_result(d.get("v"))
        except Exception as e:
            log.warning("canal de control caido: %s", e)
        finally:
            self.ws = None
            for f in self.esperando.values():
                if not f.done():
                    f.set_exception(RuntimeError("se corto el canal con el puente"))
            self.esperando.clear()

    async def llama(self, fn: str, args: dict, timeout: float = 60):
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


async def _llama(fn, args, timeout=60):
    try:
        r = await CTL.llama(fn, args, timeout)
    except Exception as e:
        return f"No hay dispositivo disponible: {e}"
    if isinstance(r, dict) and "error" in r:
        return f"Error: {r['error']}"
    return r


# ════════════════════════════════════════════════════════════
#  Herramientas
# ════════════════════════════════════════════════════════════

@mcp.tool()
async def hud_preguntar_async(pregunta: str, opciones: list[str] | None = None,
                              timeout: int = 30) -> str:
    """Lanza una pregunta al humano SIN bloquear, y devuelve un identificador.

    Patron Tasks de MCP 2026-07-28: en vez de retener el tool call mientras
    una persona decide, se devuelve un handle y se consulta con
    hud_consultar(). Usar esta variante cuando tengas otras cosas que hacer
    mientras esperas, o cuando el plazo sea largo.

    Args:
        pregunta: texto corto y sin ambiguedad.
        opciones: hasta 3 etiquetas cortas. Por defecto ["SI", "NO"].
        timeout: segundos antes de darla por no respondida.
    """
    r = await _llama("pregunta_async",
                     {"txt": pregunta, "opciones": opciones or ["SI", "NO"],
                      "timeout": timeout}, timeout=15)
    if isinstance(r, str):
        return r
    return (f"Pregunta lanzada con id '{r.get('qid')}'. Consulta el resultado "
            f"con hud_consultar('{r.get('qid')}'). Vence en {timeout}s.")


@mcp.tool()
async def hud_consultar(qid: str) -> str:
    """Consulta el resultado de una hud_preguntar_async.

    Devuelve 'pendiente' si el humano aun no ha tocado. No hagas espera activa
    consultando en bucle cerrado: haz otra cosa util entre consultas.
    """
    r = await _llama("consulta", {"qid": qid}, timeout=15)
    if isinstance(r, str):
        return r
    if r.get("estado") == "pendiente":
        return f"PENDIENTE: quedan {r.get('segundos', 0)}s para que el humano decida."
    if not r.get("respondido"):
        return ("SIN RESPUESTA: vencio el plazo. Trata el silencio como una "
                "negativa; no continues con la accion.")
    return f"El humano eligio '{r['opcion']}' (opcion {r['indice']})."


@mcp.tool()
async def hud_preguntar(pregunta: str, opciones: list[str] | None = None,
                        timeout: int = 30) -> str:
    """Pregunta al humano en la pantalla del ESP32 y ESPERA su respuesta fisica.

    Usar SIEMPRE antes de una accion irreversible o costosa: borrar ficheros,
    git push --force, enviar un correo, lanzar un deploy, gastar dinero.

    Bloquea hasta que la persona toca una opcion o hasta que vence el plazo.
    Si vence, devuelve 'sin respuesta' y NO debes asumir consentimiento:
    trata el silencio como un NO.

    Args:
        pregunta: texto corto y sin ambiguedad, maximo unas 50 letras.
        opciones: hasta 3 etiquetas cortas. Por defecto ["SI", "NO"].
        timeout: segundos de espera antes de rendirse. 30 por defecto.
    """
    r = await _llama("pregunta",
                     {"txt": pregunta, "opciones": opciones or ["SI", "NO"],
                      "timeout": timeout},
                     timeout=timeout + 10)
    if isinstance(r, str):
        return r
    if not r.get("respondido"):
        return (f"SIN RESPUESTA tras {timeout}s. El humano no confirmo. "
                f"No continues con la accion: trata esto como una negativa.")
    return (f"El humano eligio '{r['opcion']}' (opcion {r['indice']}) "
            f"en {r['segundos']}s.")


@mcp.tool()
async def hud_mostrar(id: str, titulo: str, filas: list[str],
                      acento: str = "cyan", ttl: int = 0) -> str:
    """Crea o actualiza una pantalla en el HUD circular del ESP32.

    La pantalla se anade al carrusel y el usuario navega hasta ella con los
    botones laterales. Reenviar el mismo 'id' la reemplaza sin duplicarla.

    Args:
        id: clave estable de la pantalla, ej. 'unity', 'pipeline', 'bolsa'.
        titulo: cabecera, maximo 10 letras. Se pinta en mayusculas.
        filas: hasta 6 lineas de 26 caracteres. Lo que sobre se corta:
               la pantalla es redonda y el texto largo se sale del cristal.
        acento: cyan, magenta, lime, amber, ice, blood, grey o white.
        ttl: segundos de vida. 0 = permanente hasta hud_borrar.
    """
    return await _llama("mostrar", {"id": id, "titulo": titulo, "filas": filas,
                                    "acento": acento, "ttl": ttl})


@mcp.tool()
async def hud_borrar(id: str) -> str:
    """Elimina del carrusel una pantalla creada con hud_mostrar."""
    return await _llama("borrar", {"id": id})


@mcp.tool()
async def hud_notificar(texto: str, nivel: str = "info", beep: bool = False) -> str:
    """Interrumpe al usuario con una banda superior durante unos segundos.

    No roba la navegacion ni exige respuesta: es para avisar, no para preguntar.
    Si necesitas una decision, usa hud_preguntar.

    Args:
        texto: mensaje corto.
        nivel: info, ok, warn o error. Determina el color.
        beep: si emite un pitido por el altavoz.
    """
    return await _llama("notifica", {"txt": texto, "nivel": nivel, "beep": beep})


@mcp.tool()
async def hablar(texto: str) -> str:
    """Dice algo en voz alta por el altavoz del ESP32, sin que nadie pregunte.

    Para avisos que el usuario debe oir aunque no este mirando la pantalla.
    Frases cortas y naturales: se van a pronunciar, no a leer.
    """
    return await _llama("hablar", {"texto": texto})


@mcp.tool()
async def dispositivo_estado() -> str:
    """Estado del ESP32: conexion, firmware, WiFi, memoria libre y vistas activas.

    Util antes de crear vistas (para saber cuantas caben) o para diagnosticar
    por que el usuario no esta viendo lo que crees que le mandaste.
    """
    r = await _llama("estado", {})
    return json.dumps(r, ensure_ascii=False, indent=2) if isinstance(r, dict) else r


if __name__ == "__main__":
    mcp.run()
