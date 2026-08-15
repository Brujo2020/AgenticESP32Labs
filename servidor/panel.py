#!/usr/bin/env python3
"""
Panel de control — un solo sitio para lo que antes eran tres (o cuatro):

  - claves de API          (antes: export a mano en cada shell)
  - proveedores LLM/STT/TTS (antes: editar config.yaml a mano)
  - herramientas MCP        (antes: mcps_cli.py, que sigue aqui dentro)
  - probar el agente        (antes: consola.py suelto)

    python3 panel.py              menu interactivo
    python3 panel.py --resumen    todo el estado, sin menu, para un vistazo rapido

No sustituye a mcps_cli.py ni a consola.py: los reutiliza tal cual estan,
para no duplicar logica que ya funciona.
"""
import asyncio
import getpass
import re
import sys
from pathlib import Path

import yaml

AQUI = Path(__file__).parent
sys.path.insert(0, str(AQUI))

from nucleo.entorno import carga_env, lee_todas, escribe, ENV_PATH
carga_env()

CATALOGO_MCP = yaml.safe_load((AQUI / "mcp_catalogo.yaml").read_text()) or {}
CONFIG_PATH = AQUI / "config.yaml"

V, R, A, Z, G, N = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[90m", "\033[0m"


def _config():
    return yaml.safe_load(CONFIG_PATH.read_text()) or {}


# ================================================================
#  1) Claves de API
# ================================================================
# Quien usa cada variable: se deduce del propio config.yaml (catalogo de
# proveedores) y de mcp_catalogo.yaml (entradas 'env'), no de una lista
# mantenida a mano que se desincroniza. Anadir un proveedor o un MCP nuevo
# con '${MI_CLAVE}' hace que aparezca aqui solo.
def _claves_usadas() -> dict[str, list[str]]:
    usos: dict[str, list[str]] = {}

    def registra(valor, quien):
        if isinstance(valor, str) and valor.startswith("${") and valor.endswith("}"):
            usos.setdefault(valor[2:-1], []).append(quien)

    cfg = _config()
    for capacidad, proveedores in (cfg.get("catalogo") or {}).items():
        for nombre, datos in (proveedores or {}).items():
            registra(datos.get("api_key"), f"{capacidad}:{nombre}")
            for v in re.findall(r"\$\{(\w+)\}", str(datos.get("base_url", ""))):
                usos.setdefault(v, []).append(f"{capacidad}:{nombre}")

    for nombre, entrada in CATALOGO_MCP.items():
        for v in (entrada.get("env") or {}).values():
            registra(v, f"mcp:{nombre}")
        for parte in entrada.get("command", []):
            if isinstance(parte, str):
                for v in re.findall(r"\$\{(\w+)\}", parte):
                    usos.setdefault(v, []).append(f"mcp:{nombre}")
    return usos


def _enmascara(v: str) -> str:
    if len(v) <= 8:
        return "*" * len(v)
    return v[:4] + "*" * (len(v) - 6) + v[-2:]


def claves_estado():
    usos = _claves_usadas()
    import os
    fichero = lee_todas()
    filas = []
    for clave in sorted(usos):
        en_entorno = os.getenv(clave, "")
        puesta = bool(en_entorno)
        origen = "entorno" if os.environ.get(clave) and clave not in fichero else (
                 ".env" if clave in fichero else "")
        filas.append({
            "clave": clave, "usada_por": usos[clave],
            "puesta": puesta, "origen": origen,
            "valor_enmascarado": _enmascara(en_entorno) if puesta else "",
        })
    return filas


def menu_claves():
    while True:
        filas = claves_estado()
        print(f"\n{Z}=== Claves de API ==={N}")
        print(f"{G}Fichero: {ENV_PATH} (no se sube a git; una var ya exportada en la shell manda){N}\n")
        for i, f in enumerate(filas, 1):
            estado = f"{V}puesta ({f['origen']}) {f['valor_enmascarado']}{N}" if f["puesta"] else f"{R}falta{N}"
            print(f" {i:2}. {f['clave']:22} {estado:30} {G}usada por: {', '.join(f['usada_por'])}{N}")
        print(f"\n{Z}numero{N} = poner/cambiar   {Z}b<numero>{N} = borrar del .env   {Z}q{N} = volver")
        op = input("> ").strip().lower()
        if op in ("q", "salir", ""):
            return
        if op.startswith("b") and op[1:].isdigit():
            n = int(op[1:])
            if 1 <= n <= len(filas):
                actuales = lee_todas()
                actuales.pop(filas[n - 1]["clave"], None)
                escribe(actuales)
                print(f"{A}borrada del .env (si estaba tambien exportada en la shell, sigue activa){N}")
            continue
        if op.isdigit() and 1 <= int(op) <= len(filas):
            clave = filas[int(op) - 1]["clave"]
            valor = getpass.getpass(f"Valor para {clave} (vacio = cancelar): ")
            if not valor:
                continue
            actuales = lee_todas()
            actuales[clave] = valor
            escribe(actuales)
            import os
            os.environ[clave] = valor    # activa ya en este proceso, sin reiniciar
            print(f"{V}guardada en {ENV_PATH.name}{N}")
        else:
            print(f"{R}opcion no valida{N}")


# ================================================================
#  2) Proveedores LLM / STT / TTS
# ================================================================
def _disponible(capacidad: str, nombre: str, cfg: dict) -> tuple[bool, str]:
    import os
    datos = ((cfg.get("catalogo") or {}).get(capacidad) or {}).get(nombre)
    if not datos:
        return False, "no esta en el catalogo"
    backend = datos.get("backend", "")
    if backend == "macos":
        return sys.platform == "darwin", "solo en macOS"
    if backend == "mlx" or "mlx" in nombre:
        try:
            import mlx  # noqa: F401
            return True, ""
        except ImportError:
            return False, "falta el paquete mlx (pip install mlx-lm / mlx-whisper)"
    clave = datos.get("api_key", "")
    if clave.startswith("${") and clave.endswith("}"):
        var = clave[2:-1]
        if not os.getenv(var):
            return False, f"falta {var}"
    return True, ""


def guarda_proveedores(cfg: dict):
    """Reescribe solo la seccion 'proveedores:', igual que mcps_cli con mcp_servers."""
    texto = CONFIG_PATH.read_text()
    nuevo = yaml.safe_dump({"proveedores": cfg["proveedores"]}, allow_unicode=True, sort_keys=False)
    texto = re.sub(r"\nproveedores:.*?(?=\n#|\ncatalogo:)", "\n" + nuevo, texto, count=1, flags=re.S)
    CONFIG_PATH.write_text(texto)


def menu_proveedores():
    while True:
        cfg = _config()
        prov = cfg.get("proveedores", {})
        print(f"\n{Z}=== Proveedores (LLM / STT / TTS) ==={N}")
        capacidades = ["llm", "stt", "tts"]
        for i, cap in enumerate(capacidades, 1):
            orden = prov.get(cap, [])
            print(f"\n {i}. {cap.upper()}  orden actual: {V}{' > '.join(orden) or '(vacio)'}{N}")
            for nombre in (cfg.get("catalogo", {}).get(cap) or {}):
                ok, motivo = _disponible(cap, nombre, cfg)
                marca = f"{V}listo{N}" if ok else f"{R}{motivo}{N}"
                en_uso = " (activo)" if orden and orden[0] == nombre else ""
                print(f"      - {nombre:14} {marca}{en_uso}")
        print(f"\n{Z}1/2/3{N} = reordenar esa capacidad   {Z}q{N} = volver")
        op = input("> ").strip().lower()
        if op in ("q", "salir", ""):
            return
        if op in ("1", "2", "3"):
            cap = capacidades[int(op) - 1]
            disponibles = list((cfg.get("catalogo", {}).get(cap) or {}).keys())
            print(f"Disponibles en el catalogo: {', '.join(disponibles)}")
            nuevo = input(f"Nuevo orden para {cap} (separado por comas): ").strip()
            if not nuevo:
                continue
            lista = [x.strip() for x in nuevo.split(",") if x.strip()]
            desconocidos = [x for x in lista if x not in disponibles]
            if desconocidos:
                print(f"{R}no estan en el catalogo: {', '.join(desconocidos)}{N}")
                continue
            cfg.setdefault("proveedores", {})[cap] = lista
            guarda_proveedores(cfg)
            print(f"{V}guardado{N}")
        else:
            print(f"{R}opcion no valida{N}")


# ================================================================
#  3) Herramientas MCP — se reutiliza mcps_cli.py tal cual
# ================================================================
def menu_mcp():
    import mcps_cli
    mcps_cli.menu()


# ================================================================
#  4) Probar todo
# ================================================================
async def _probar_proveedores():
    from proveedores import cadenas_desde_config
    cfg = _config()
    cadenas = cadenas_desde_config(cfg)
    print(f"\n{Z}--- proveedores ---{N}")
    for cap, cadena in cadenas.items():
        if cadena.activo:
            print(f" {V}{cap:5}{N} -> {cadena.activo.nombre}")
        else:
            print(f" {R}{cap:5}{N} -> ninguno disponible")


async def _probar_mcp():
    import mcps_cli
    print(f"\n{Z}--- MCP ---{N}")
    await mcps_cli.probar()


async def probar_todo():
    await _probar_proveedores()
    await _probar_mcp()


def resumen():
    """--resumen: todo el estado de un vistazo, sin menu. Para un vistazo rapido
    o para pegar en un issue cuando algo no arranca."""
    asyncio.run(probar_todo())
    print(f"\n{Z}--- claves de API ---{N}")
    for f in claves_estado():
        estado = f"{V}OK{N}" if f["puesta"] else f"{R}falta{N}"
        print(f" {f['clave']:22} {estado}")


# ================================================================
#  Menu principal
# ================================================================
def menu():
    while True:
        print(f"\n{Z}========================================{N}")
        print(f"{Z}  Panel de control — Asistente ESP32{N}")
        print(f"{Z}========================================{N}")
        print(" 1. Claves de API")
        print(" 2. Proveedores (LLM / STT / TTS)")
        print(" 3. Herramientas MCP")
        print(" 4. Probar todo")
        print(" 5. Consola de prueba (chat con el agente, sin la placa)")
        print(" q. Salir")
        op = input("> ").strip().lower()
        if op in ("q", "salir", ""):
            break
        elif op == "1":
            menu_claves()
        elif op == "2":
            menu_proveedores()
        elif op == "3":
            menu_mcp()
        elif op == "4":
            asyncio.run(probar_todo())
            input("\nintro para continuar...")
        elif op == "5":
            import subprocess
            subprocess.run([sys.executable, str(AQUI / "consola.py")])
        else:
            print(f"{R}opcion no valida{N}")


if __name__ == "__main__":
    if "--resumen" in sys.argv:
        resumen()
    else:
        menu()
