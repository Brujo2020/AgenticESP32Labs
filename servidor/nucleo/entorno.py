"""
Carga servidor/.env al entorno del proceso, si existe.

Motivo: hasta ahora la unica forma de poner una API key era `export` en la
shell (ver PROVEEDORES.md, MCP.md) — se pierde al abrir una terminal nueva y
no hay un solo sitio donde ver "que claves tengo puestas". Con esto,
panel.py puede escribir/leer un fichero .env normal y los tres puntos de
entrada (websocket_bridge.py, mcps_cli.py, consola.py) lo cargan solos.

No sustituye al entorno real: una variable ya exportada en la shell gana
siempre a la del fichero. Así, en CI o en un contenedor donde las claves
llegan por variables de entorno de verdad, .env no interfiere.
"""
import logging
from pathlib import Path

log = logging.getLogger("entorno")

AQUI = Path(__file__).resolve().parent.parent   # servidor/
ENV_PATH = AQUI / ".env"


def _parsea_linea(linea: str):
    linea = linea.strip()
    if not linea or linea.startswith("#") or "=" not in linea:
        return None
    clave, _, valor = linea.partition("=")
    clave = clave.strip()
    valor = valor.strip()
    # Comillas opcionales, para poder pegar valores con espacios o '#'
    if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in ("'", '"'):
        valor = valor[1:-1]
    return (clave, valor) if clave else None


def carga_env(ruta: Path = ENV_PATH) -> int:
    """Aplica ruta a os.environ. Devuelve cuantas variables se cargaron.

    Una variable YA presente en el entorno CON VALOR nunca se pisa: .env solo
    rellena huecos. Pero una variable exportada vacia ("export X=" suelto en
    el .zshrc, por ejemplo, de un intento anterior) SI se rellena: una clave
    vacia no es una clave real puesta a proposito, es un residuo, y dejarla
    vacia rompia panel.py en silencio (la API devolvia 401 sin que se notara
    por que: "puesta" en el panel, pero vacia en verdad).
    """
    import os
    if not ruta.exists():
        return 0
    cargadas = 0
    for linea in ruta.read_text().splitlines():
        par = _parsea_linea(linea)
        if not par:
            continue
        clave, valor = par
        if os.environ.get(clave):     # presente Y con contenido: no se toca
            continue
        os.environ[clave] = valor
        cargadas += 1
    if cargadas:
        log.info("%s: %d variable(s) cargadas desde %s", "entorno", cargadas, ruta.name)
    return cargadas


def lee_todas(ruta: Path = ENV_PATH) -> dict:
    """Las claves tal cual estan en el fichero, sin tocar os.environ."""
    if not ruta.exists():
        return {}
    out = {}
    for linea in ruta.read_text().splitlines():
        par = _parsea_linea(linea)
        if par:
            out[par[0]] = par[1]
    return out


def escribe(valores: dict, ruta: Path = ENV_PATH):
    """Reescribe el fichero completo a partir de un dict clave->valor.

    Se reescribe entero (no se editan lineas sueltas): para un fichero de
    unas pocas claves de API es mas simple y mas dificil de dejar a medias
    que un editor incremental.
    """
    lineas = ["# Claves de API — generado por panel.py. No subir a git.", ""]
    for k in sorted(valores):
        v = valores[k]
        if v == "":
            continue
        # Comillas si el valor tiene espacios o '#', para que no se rompa al releer
        necesita_comillas = " " in v or "#" in v
        lineas.append(f'{k}="{v}"' if necesita_comillas else f"{k}={v}")
    ruta.write_text("\n".join(lineas) + "\n")
    try:
        ruta.chmod(0o600)      # son secretos: solo el dueño los lee
    except OSError:
        pass
