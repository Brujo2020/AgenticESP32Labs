#!/usr/bin/env python3
"""
MCP 'vigia' — la primera funcion que ejercita el patron completo.

Vigila un repositorio y las apps creativas, publica el estado como vista en el
HUD, y para lo irreversible pide aprobacion fisica en el dispositivo antes de
tocar nada. Es el ejemplo canonico de por que el ESP32 deja de ser una pantalla:

    agente -> vigia.git_push(...) -> hud_preguntar("PUSH A MASTER?")
           -> el humano toca SI en el cacharro -> se ejecuta
           -> el humano no toca nada -> NO se ejecuta

Nota deliberada: este servidor NUNCA ejecuta una accion destructiva sin que la
aprobacion haya vuelto del dispositivo. Si el HUD no esta disponible, no hay
camino alternativo: se rechaza. Un mecanismo de aprobacion con puerta trasera
no es un mecanismo de aprobacion.
"""
import asyncio, json, os, subprocess, sys, logging

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("Falta el paquete MCP:  pip install mcp", file=sys.stderr); sys.exit(1)
try:
    import websockets
except ImportError:
    print("Falta websockets:  pip install websockets", file=sys.stderr); sys.exit(1)

PUENTE = os.getenv("HUD_BRIDGE", "ws://127.0.0.1:8765")
REPO = os.getenv("VIGIA_REPO", os.getcwd())

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
log = logging.getLogger("vigia")
mcp = FastMCP("vigia")


# ---------------------------------------------------------------
#  Canal de control (mismo patron que mcps/dispositivo.py)
# ---------------------------------------------------------------
class Control:
    def __init__(self):
        self.ws = None; self.rid = 0; self.esperando = {}
        self._lock = asyncio.Lock()

    async def _conecta(self):
        if self.ws:
            return
        self.ws = await websockets.connect(PUENTE, max_size=None)
        await self.ws.send(json.dumps({"t": "hola", "rol": "control"}))
        asyncio.create_task(self._lee())

    async def _lee(self):
        try:
            async for m in self.ws:
                if isinstance(m, bytes):
                    continue
                d = json.loads(m)
                if d.get("t") == "res":
                    f = self.esperando.pop(d.get("rid"), None)
                    if f and not f.done():
                        f.set_result(d.get("v"))
        except Exception as e:
            log.warning("canal caido: %s", e)
        finally:
            self.ws = None
            for f in self.esperando.values():
                if not f.done():
                    f.set_exception(RuntimeError("canal cerrado"))
            self.esperando.clear()

    async def llama(self, fn, args, timeout=60):
        async with self._lock:
            await self._conecta()
            self.rid += 1; rid = self.rid
        fut = asyncio.get_running_loop().create_future()
        self.esperando[rid] = fut
        await self.ws.send(json.dumps({"t": "cmd", "rid": rid, "fn": fn, "args": args}))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self.esperando.pop(rid, None)
            return {"error": f"el puente no respondio en {timeout}s"}


CTL = Control()


async def _aprueba(pregunta: str, timeout: int = 30) -> tuple[bool, str]:
    """Pide aprobacion fisica. Devuelve (autorizado, motivo).

    Cualquier cosa que no sea un SI explicito devuelto por el dispositivo
    cuenta como negativa: fallo de canal, timeout, o eleccion distinta.
    """
    try:
        r = await CTL.llama("pregunta",
                            {"txt": pregunta, "opciones": ["SI", "NO"],
                             "timeout": timeout},
                            timeout=timeout + 10)
    except Exception as e:
        return False, f"no se pudo pedir aprobacion ({e}); no se ejecuta nada"
    if isinstance(r, str):
        return False, r
    if isinstance(r, dict) and r.get("error"):
        return False, f"la guardia rechazo la peticion: {r['error']}"
    if not r.get("respondido"):
        return False, f"nadie confirmo en {timeout}s; se trata como negativa"
    if r.get("indice") != 0:
        return False, f"el humano eligio '{r.get('opcion')}'"
    return True, f"aprobado en {r.get('segundos')}s"


def _git(*args, cwd=None) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=cwd or REPO,
                       capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


# ---------------------------------------------------------------
#  Herramientas
# ---------------------------------------------------------------

@mcp.tool()
async def estado_repo(publicar: bool = True) -> str:
    """Lee el estado del repositorio y opcionalmente lo publica en el HUD.

    Args:
        publicar: si ademas crea la vista 'repo' en la pantalla del ESP32.
    """
    _, rama = _git("rev-parse", "--abbrev-ref", "HEAD")
    _, sucio = _git("status", "--porcelain")
    _, ultimo = _git("log", "-1", "--format=%h %s")
    _, delante = _git("rev-list", "--count", "@{u}..HEAD")

    n_cambios = len([l for l in sucio.splitlines() if l.strip()])
    resumen = {"rama": rama, "sin_confirmar": n_cambios,
               "sin_publicar": delante if delante.isdigit() else "?",
               "ultimo": ultimo}

    if publicar:
        filas = [
            {"txt": f"RAMA {rama}", "color": "cyan"},
            {"txt": f"{n_cambios} SIN CONFIRMAR",
             "color": "amber" if n_cambios else "lime"},
            {"txt": f"{resumen['sin_publicar']} SIN PUBLICAR",
             "color": "amber" if resumen["sin_publicar"] not in ("0", "?") else "lime"},
            {"txt": ultimo[:26], "color": "grey"},
        ]
        await CTL.llama("mostrar", {"id": "repo", "titulo": "REPO",
                                    "filas": filas, "acento": "cyan", "orden": 10})
    return json.dumps(resumen, ensure_ascii=False, indent=2)


@mcp.tool()
async def publicar_cambios(rama: str = "", timeout: int = 45) -> str:
    """Hace git push, pero SOLO si el humano lo aprueba en el dispositivo.

    Pide confirmacion fisica en el HUD antes de publicar. Si nadie confirma,
    no publica: el silencio nunca cuenta como permiso.

    Args:
        rama: rama a publicar. Vacio = la rama actual.
        timeout: segundos que se espera la aprobacion.
    """
    if not rama:
        _, rama = _git("rev-parse", "--abbrev-ref", "HEAD")
    _, n = _git("rev-list", "--count", "@{u}..HEAD")
    if n == "0":
        return f"No hay nada que publicar en '{rama}'."

    autorizado, motivo = await _aprueba(f"PUBLICAR {n} COMMITS EN {rama}?", timeout)
    if not autorizado:
        return f"NO PUBLICADO: {motivo}."

    codigo, salida = _git("push", "origin", rama)
    if codigo == 0:
        await CTL.llama("notifica", {"txt": f"PUBLICADO EN {rama}",
                                     "nivel": "ok", "beep": True})
        return f"Publicado en '{rama}' ({motivo}).\n{salida}"
    await CTL.llama("notifica", {"txt": "FALLO EL PUSH", "nivel": "error"})
    return f"Fallo el push: {salida}"


@mcp.tool()
async def confirmar_accion(descripcion: str, timeout: int = 30) -> str:
    """Pide aprobacion fisica generica para una accion que vas a hacer tu.

    Usar antes de cualquier operacion irreversible que no tenga herramienta
    propia: borrar ficheros, enviar correo, gastar dinero, desplegar.
    Devuelve si el humano autorizo. Si no lo hizo, NO procedas.

    Args:
        descripcion: que se va a hacer, corto y sin ambiguedad.
        timeout: segundos de espera.
    """
    autorizado, motivo = await _aprueba(descripcion.upper()[:52], timeout)
    return (f"AUTORIZADO ({motivo}). Puedes proceder."
            if autorizado else
            f"NO AUTORIZADO: {motivo}. No ejecutes la accion ni busques "
            f"una via alternativa: pregunta al usuario por otro canal.")


@mcp.tool()
async def vigilar(segundos: int = 300, intervalo: int = 15) -> str:
    """Vigila el repositorio y refresca la vista del HUD periodicamente.

    Args:
        segundos: cuanto tiempo vigilar en total.
        intervalo: cada cuanto refrescar.
    """
    vueltas = max(1, segundos // max(5, intervalo))
    previo = None
    cambios = 0
    for _ in range(vueltas):
        _, actual = _git("log", "-1", "--format=%h")
        if previo and actual != previo:
            cambios += 1
            await CTL.llama("notifica", {"txt": f"NUEVO COMMIT {actual}",
                                         "nivel": "info"})
        previo = actual
        await estado_repo(publicar=True)
        await asyncio.sleep(intervalo)
    return f"Vigilancia terminada: {vueltas} comprobaciones, {cambios} commits nuevos."


if __name__ == "__main__":
    mcp.run()
