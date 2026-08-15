#!/usr/bin/env python3
"""
hud — maneja el HUD del ESP32 desde la linea de comandos.

Se conecta al puente como cliente de control, igual que hace el MCP
'dispositivo'. Sirve para probar el canal sin depender de Claude Desktop,
y para automatizar cosas desde cualquier script o cron del Mac.

    ./hud.py estado
    ./hud.py mostrar maquina "CPU 34%" "RAM 18/32 GB" "BLENDER 41%"
    ./hud.py decir "el build de unity termino"
    ./hud.py avisar "PR 42 aprobado" --nivel ok
    ./hud.py preguntar "PUBLICAR EN MASTER?" SI NO

Con firmware v1 (sin protocolo v2) 'mostrar' admite tres destinos, que se
corresponden con pantallas que el firmware ya sabe pintar:

    senales -> pantalla SENALES      maquina -> MAQUINA      forja -> FORJA

'preguntar' necesita el protocolo v2 y avisa si no esta disponible.
"""
import argparse, asyncio, json, os, sys

try:
    import websockets
except ImportError:
    print("Falta websockets:  pip install websockets", file=sys.stderr)
    sys.exit(1)

PUENTE = os.getenv("HUD_BRIDGE", "ws://127.0.0.1:8765")


async def manda(fn: str, args: dict, espera: float = 60):
    """Una orden y su respuesta. Conexion corta: esto es una herramienta CLI."""
    try:
        async with websockets.connect(PUENTE, max_size=None,
                                      open_timeout=5) as ws:
            await ws.send(json.dumps({"t": "hola", "rol": "control"}))
            await ws.send(json.dumps({"t": "cmd", "rid": 1,
                                      "fn": fn, "args": args}))
            fin = asyncio.get_event_loop().time() + espera
            while asyncio.get_event_loop().time() < fin:
                msg = await asyncio.wait_for(ws.recv(), timeout=espera)
                if isinstance(msg, bytes):
                    continue
                d = json.loads(msg)
                if d.get("t") == "res":
                    return d.get("v")
            return {"error": "sin respuesta del puente"}
    except (OSError, asyncio.TimeoutError) as e:
        return {"error": f"no se pudo hablar con el puente en {PUENTE}: {e}. "
                         f"Arranca servidor/websocket_bridge.py"}


def pinta(v):
    if isinstance(v, dict):
        if v.get("error"):
            print(f"error: {v['error']}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(v, ensure_ascii=False, indent=2))
    else:
        print(v)


def main():
    p = argparse.ArgumentParser(
        prog="hud", description="Maneja el HUD del ESP32 desde el Mac.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("estado", help="conexion, protocolo y destinos disponibles")

    m = sub.add_parser("mostrar", help="pinta lineas en una pantalla")
    m.add_argument("destino", help="senales | maquina | forja (o un id libre con v2)")
    m.add_argument("lineas", nargs="+", help="una linea por argumento")
    m.add_argument("--titulo", default="", help="solo con protocolo v2")
    m.add_argument("--acento", default="cyan",
                   choices=["cyan", "magenta", "lime", "amber", "ice",
                            "blood", "grey", "white"])

    b = sub.add_parser("borrar", help="vacia una pantalla")
    b.add_argument("destino")

    d = sub.add_parser("decir", help="lo dice en voz alta por el altavoz")
    d.add_argument("texto")

    a = sub.add_parser("avisar", help="mensaje corto de aviso")
    a.add_argument("texto")
    a.add_argument("--nivel", default="info",
                   choices=["info", "ok", "warn", "error"])
    a.add_argument("--beep", action="store_true")

    q = sub.add_parser("preguntar", help="pregunta y espera un toque fisico")
    q.add_argument("texto")
    q.add_argument("opciones", nargs="*", default=["SI", "NO"])
    q.add_argument("--espera", type=int, default=30, help="segundos")

    o = p.parse_args()

    if o.cmd == "estado":
        pinta(asyncio.run(manda("estado", {})))
    elif o.cmd == "mostrar":
        pinta(asyncio.run(manda("mostrar", {
            "id": o.destino, "titulo": o.titulo or o.destino,
            "filas": o.lineas, "acento": o.acento})))
    elif o.cmd == "borrar":
        pinta(asyncio.run(manda("borrar", {"id": o.destino})))
    elif o.cmd == "decir":
        pinta(asyncio.run(manda("hablar", {"texto": o.texto})))
    elif o.cmd == "avisar":
        pinta(asyncio.run(manda("notifica", {"txt": o.texto, "nivel": o.nivel,
                                             "beep": o.beep})))
    elif o.cmd == "preguntar":
        r = asyncio.run(manda("pregunta",
                              {"txt": o.texto, "opciones": o.opciones,
                               "timeout": o.espera},
                              espera=o.espera + 10))
        pinta(r)
        # Codigo de salida util para scripts: 0 si aprobo la primera opcion.
        if isinstance(r, dict) and r.get("respondido") and r.get("indice") == 0:
            sys.exit(0)
        sys.exit(2)


if __name__ == "__main__":
    main()
