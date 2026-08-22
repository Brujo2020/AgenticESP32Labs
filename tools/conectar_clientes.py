#!/usr/bin/env python3
"""
Conecta el ESP32 a tus clientes MCP: Claude Desktop, Claude Code, Cursor...

El HUD deja de ser un aparato que solo habla con su propio servidor y pasa a
ser una herramienta que cualquier agente puede usar: pintar vistas, avisar,
hablar por el altavoz y -- lo mas util -- PEDIR APROBACION FISICA antes de
algo irreversible. Un agente a punto de hacer un push --force te lo pregunta
en la pantalla que tienes en el escritorio, no en una ventana tapada.

    python3 tools/conectar_clientes.py                 # instala en lo que encuentre
    python3 tools/conectar_clientes.py --revisar       # solo mira, no toca nada
    python3 tools/conectar_clientes.py --servidor IP   # otra IP del puente
    python3 tools/conectar_clientes.py --quitar        # deshace la instalacion

Antes de escribir hace una copia de seguridad con fecha de cada fichero, y
solo toca la entrada 'esp32' dentro de mcpServers: el resto de tu
configuracion se conserva tal cual.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVIDOR = REPO / "servidor"
MCP_SCRIPT = SERVIDOR / "mcps" / "dispositivo.py"

# El nombre con el que aparecera en el cliente. Estable: reinstalar actualiza
# la entrada en vez de duplicarla.
NOMBRE = "esp32"

IP_POR_DEFECTO = "56.125.193.142"
PUERTO = 8765

V, R, A, Z, G, N = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[90m", "\033[0m"


# ============================================================
#  Clientes conocidos
# ============================================================
# Cada cliente guarda su configuracion en un sitio distinto, pero todos usan
# la misma forma: un objeto "mcpServers" con una entrada por servidor.
def clientes() -> list[dict]:
    h = Path.home()
    return [
        {"nombre": "Claude Desktop",
         "ruta": h / "Library/Application Support/Claude/claude_desktop_config.json"
                 if sys.platform == "darwin" else
                 h / ".config/Claude/claude_desktop_config.json",
         "clave": "mcpServers",
         "aviso": "cierra y vuelve a abrir Claude Desktop para que lo cargue"},

        {"nombre": "Claude Code",
         "ruta": h / ".claude.json",
         "clave": "mcpServers",
         "aviso": "abre una sesion nueva de Claude Code"},

        {"nombre": "Cursor",
         "ruta": h / ".cursor/mcp.json",
         "clave": "mcpServers",
         "aviso": "Cursor > Settings > MCP, y comprueba que aparece en verde"},

        {"nombre": "Windsurf",
         "ruta": h / ".codeium/windsurf/mcp_config.json",
         "clave": "mcpServers",
         "aviso": "reinicia Windsurf"},

        {"nombre": "VS Code (Continue)",
         "ruta": h / ".continue/config.json",
         "clave": "mcpServers",
         "aviso": "recarga la ventana de VS Code"},
    ]


# ============================================================
#  Con que Python se lanza el MCP
# ============================================================
def interprete() -> tuple[str, str]:
    """Devuelve (ruta_python, explicacion).

    Se prefiere el venv del servidor: ahi estan 'mcp' y 'websockets', que es
    lo que dispositivo.py necesita. Si se usara el python del sistema, el
    cliente MCP arrancaria el proceso, fallaria el import y el servidor
    aparaceria en rojo sin decir por que.
    """
    venv = SERVIDOR / "venv" / "bin" / "python3"
    # Se comprueba EJECUTANDOLO, no con exists(): en un venv, bin/python3 es
    # un enlace simbolico, y exists() sigue el enlace. Si el venv se creo con
    # un Python que ya no esta (o se mira desde otra maquina), exists() dice
    # que no hay venv y se caeria al python del sistema sin avisar.
    if venv.is_file() or venv.is_symlink():
        try:
            r = subprocess.run([str(venv), "-c", "pass"],
                               capture_output=True, timeout=10)
            if r.returncode == 0:
                return str(venv), "venv del servidor"
            return sys.executable, (
                f"el venv existe pero no arranca ({venv}); se usa el python "
                f"del sistema")
        except (OSError, subprocess.SubprocessError):
            return sys.executable, f"el venv existe pero no se puede ejecutar ({venv})"
    return sys.executable, "python del sistema (revisa que tenga 'mcp' y 'websockets')"


def comprueba_dependencias(py: str) -> list[str]:
    faltan = []
    for mod in ("mcp", "websockets"):
        r = subprocess.run([py, "-c", f"import {mod}"], capture_output=True)
        if r.returncode != 0:
            faltan.append(mod)
    return faltan


def entrada_mcp(ip: str, py: str) -> dict:
    return {
        "command": py,
        "args": [str(MCP_SCRIPT)],
        "env": {
            "HUD_BRIDGE": f"ws://{ip}:{PUERTO}",
            # dispositivo.py hace 'from nucleo...' indirectamente a traves de
            # los imports del proyecto; sin esto el proceso no encuentra nada.
            "PYTHONPATH": str(SERVIDOR),
        },
    }


# ============================================================
#  Escritura segura
# ============================================================
def lee_json(ruta: Path) -> dict:
    if not ruta.exists() or ruta.stat().st_size == 0:
        return {}
    try:
        return json.loads(ruta.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"{R}{ruta} no es JSON valido ({e}).{N}\n"
            f"Arreglalo o muevelo antes de seguir: no se toca a ciegas un "
            f"fichero de configuracion que ya esta roto.")


def escribe_json(ruta: Path, datos: dict):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    # Copia de seguridad con fecha antes de tocar nada.
    if ruta.exists():
        copia = ruta.with_suffix(ruta.suffix + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(ruta, copia)
        print(f"   {G}copia previa: {copia.name}{N}")
    # Escritura atomica: un corte a media escritura no puede dejar el fichero
    # truncado y el cliente sin arrancar.
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(ruta)


# ============================================================
#  Comprobaciones previas
# ============================================================
def puente_vivo(ip: str) -> bool:
    try:
        with socket.create_connection((ip, PUERTO), timeout=4):
            return True
    except OSError:
        return False


def revisa(ip: str, py: str, motivo_py: str):
    print(f"\n{Z}=== Comprobaciones ==={N}")
    ok = True

    if MCP_SCRIPT.exists():
        print(f" {V}OK{N}   {MCP_SCRIPT.relative_to(REPO)}")
    else:
        print(f" {R}FALTA{N} {MCP_SCRIPT}")
        ok = False

    print(f" {V}OK{N}   interprete: {py}  {G}({motivo_py}){N}")

    faltan = comprueba_dependencias(py)
    if faltan:
        print(f" {R}FALTA{N} paquetes {', '.join(faltan)} en ese Python")
        print(f"        instalalos con:  {py} -m pip install {' '.join(faltan)}")
        ok = False
    else:
        print(f" {V}OK{N}   'mcp' y 'websockets' disponibles")

    if puente_vivo(ip):
        print(f" {V}OK{N}   el puente responde en {ip}:{PUERTO}")
    else:
        print(f" {A}AVISO{N} no se llega a {ip}:{PUERTO}")
        print(f"        Los clientes se configuran igual, pero las herramientas")
        print(f"        fallaran hasta que el puente este arriba.")
    return ok


# ============================================================
#  Instalar / quitar
# ============================================================
def instala(ip: str, py: str, quitar: bool):
    entrada = entrada_mcp(ip, py)
    encontrados = 0

    for c in clientes():
        ruta: Path = c["ruta"]
        # Solo se tocan los clientes que ya existen. Crear el fichero de
        # configuracion de una app que no esta instalada no ayuda a nadie.
        if not ruta.exists():
            print(f"\n {G}—  {c['nombre']}: no instalado ({ruta}){N}")
            continue

        encontrados += 1
        print(f"\n {Z}>> {c['nombre']}{N}  {G}{ruta}{N}")
        datos = lee_json(ruta)
        servidores = datos.setdefault(c["clave"], {})

        if quitar:
            if servidores.pop(NOMBRE, None) is None:
                print(f"   {G}no estaba configurado, nada que quitar{N}")
                continue
            escribe_json(ruta, datos)
            print(f"   {V}quitado '{NOMBRE}'{N} — {c['aviso']}")
            continue

        ya = servidores.get(NOMBRE)
        if ya == entrada:
            print(f"   {V}ya estaba al dia{N}, no se toca el fichero")
            continue

        servidores[NOMBRE] = entrada
        escribe_json(ruta, datos)
        print(f"   {V}{'actualizado' if ya else 'anadido'} '{NOMBRE}'{N} — {c['aviso']}")

    if encontrados == 0:
        print(f"\n{A}No se encontro ningun cliente MCP instalado.{N}")
        print("Si usas otro, anade esto a mano en su seccion 'mcpServers':\n")
        print(json.dumps({NOMBRE: entrada}, indent=2))
    return encontrados


def main():
    ap = argparse.ArgumentParser(description="Conecta el ESP32 a tus clientes MCP")
    ap.add_argument("--servidor", default=IP_POR_DEFECTO,
                    help=f"IP donde corre el puente (por defecto {IP_POR_DEFECTO})")
    ap.add_argument("--revisar", action="store_true", help="solo comprobar, no escribir")
    ap.add_argument("--quitar", action="store_true", help="quitar la configuracion")
    a = ap.parse_args()

    py, motivo = interprete()
    print(f"{Z}Repositorio:{N} {REPO}")
    print(f"{Z}Puente:{N}      ws://{a.servidor}:{PUERTO}")

    ok = revisa(a.servidor, py, motivo)

    if a.revisar:
        print(f"\n{G}--revisar: no se ha escrito nada.{N}")
        return

    if not ok and not a.quitar:
        print(f"\n{R}Hay problemas que arreglar antes de instalar (ver arriba).{N}")
        print(f"{G}Puedes forzar la instalacion igualmente volviendo a lanzarlo "
              f"cuando esten resueltos.{N}")
        return

    print(f"\n{Z}=== {'Quitando' if a.quitar else 'Instalando'} ==={N}")
    n = instala(a.servidor, py, a.quitar)

    if n and not a.quitar:
        print(f"\n{V}Listo.{N} Reinicia el cliente y pidele algo como:")
        print(f'  {G}"muestrame en el ESP32 una vista con el estado del build"{N}')
        print(f'  {G}"preguntame en la pantalla si puedes borrar esa rama"{N}')
        print(f"\nHerramientas que gana: hud_mostrar, hud_preguntar, hud_notificar,")
        print(f"hablar, hud_configurar, hud_reiniciar y dispositivo_estado.")


if __name__ == "__main__":
    main()
