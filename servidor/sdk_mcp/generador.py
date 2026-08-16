"""
Generador: crea un MCP nuevo escribiendo mcp_catalogo.yaml y config.yaml en
una sola operacion atomica.

Antes (mcps_cli.alterna) solo activaba/desactivaba entradas que ya existian
en el catalogo, escritas a mano. Esto cubre el paso anterior: dar de alta la
entrada nueva. Ataca directo el bug ya documentado en MEJORAS.md ("esta en
tools_to_enable pero no en mcp_servers"): las dos escrituras van juntas o
ninguna.
"""
import re
from pathlib import Path

import yaml

AQUI = Path(__file__).resolve().parent.parent   # servidor/
CATALOGO_PATH = AQUI / "mcp_catalogo.yaml"
CONFIG_PATH = AQUI / "config.yaml"

PLANTILLA_CODIGO = '''"""
MCP: {nombre} — {descripcion}

Generado por sdk_mcp.generador. Reemplaza los cuerpos de las tools de
ejemplo por la logica real.
"""
from sdk_mcp import MCPBase

m = MCPBase("{nombre}")


@m.tool()
async def {nombre}_ejemplo() -> dict:
    """TODO: describe que hace esta tool."""
    return {{"pendiente": "implementar {nombre}_ejemplo"}}


if __name__ == "__main__":
    m.run()
'''


class MCPExistente(Exception):
    """Ya hay una entrada con ese nombre en el catalogo."""


def crear_mcp(
    nombre: str,
    descripcion: str,
    categoria: str = "local",
    activar: bool = True,
    declarativo: dict | None = None,
    env: dict | None = None,
    requisitos: str = "",
) -> Path:
    """Da de alta un MCP nuevo. Devuelve la ruta del archivo creado (o el
    directorio de servidor/ si es declarativo, sin archivo .py propio).

    `declarativo`, si se pasa, evita escribir codigo: se guarda tal cual en
    el catalogo bajo la clave 'declarativo' (ver sdk_mcp/declarativo.py) y
    mcp_pool/panel_api saben interpretarlo sin un mcps/<nombre>.py.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_]*", nombre):
        raise ValueError("el nombre debe ser minusculas, numeros y '_', empezando por letra")

    catalogo = yaml.safe_load(CATALOGO_PATH.read_text()) or {}
    if nombre in catalogo:
        raise MCPExistente(f"'{nombre}' ya existe en {CATALOGO_PATH.name}")

    ruta_archivo = None
    if declarativo:
        entrada_catalogo = {
            "categoria": categoria,
            "descripcion": descripcion,
            "declarativo": declarativo,
        }
        comando = None  # mcp_pool interpreta 'declarativo' sin subproceso propio
    else:
        ruta_archivo = AQUI / "mcps" / f"{nombre}.py"
        if ruta_archivo.exists():
            raise MCPExistente(f"ya existe {ruta_archivo}")
        ruta_archivo.write_text(
            PLANTILLA_CODIGO.format(nombre=nombre, descripcion=descripcion)
        )
        comando = ["python3", f"mcps/{nombre}.py"]
        entrada_catalogo = {
            "categoria": categoria,
            "descripcion": descripcion,
            "command": comando,
        }
        if requisitos:
            entrada_catalogo["requisitos"] = requisitos
        if env:
            entrada_catalogo["env"] = env

    catalogo[nombre] = entrada_catalogo
    CATALOGO_PATH.write_text(yaml.safe_dump(catalogo, allow_unicode=True, sort_keys=False))

    if activar:
        cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        cfg.setdefault("mcp_servers", {})
        cfg.setdefault("tools_to_enable", [])
        if comando:
            srv = {"type": "subprocess", "command": comando}
            if env:
                srv["env"] = env
            cfg["mcp_servers"][nombre] = srv
        else:
            cfg["mcp_servers"][nombre] = {"type": "declarativo"}
        if nombre not in cfg["tools_to_enable"]:
            cfg["tools_to_enable"].append(nombre)
        _guarda_config(cfg)

    return ruta_archivo or CATALOGO_PATH


def _guarda_config(cfg: dict):
    """Mismo patron que mcps_cli.guarda(): reescribe solo mcp_servers y
    tools_to_enable para no perder comentarios ni el resto del archivo."""
    texto = CONFIG_PATH.read_text()
    nuevo = yaml.safe_dump(
        {"mcp_servers": cfg["mcp_servers"], "tools_to_enable": cfg["tools_to_enable"]},
        allow_unicode=True, sort_keys=False,
    )
    nuevo_texto = re.sub(
        r"\nmcp_servers:.*?(?=\nwebsocket:)", "\n" + nuevo + "\n", texto, flags=re.S
    )
    if nuevo_texto == texto:
        raise RuntimeError(
            "no se encontro el bloque 'mcp_servers: ... websocket:' en config.yaml; "
            "revisa que la estructura no haya cambiado"
        )
    CONFIG_PATH.write_text(nuevo_texto)
