#!/usr/bin/env python3
"""
API web del panel de administracion (spec 001-panel-administracion-mcp).

Reusa panel.py y mcps_cli.py tal cual estan -- este archivo no reimplementa
su logica, solo la expone por HTTP para que panel_web/ (el front) pueda
hablarle. Proceso separado del bridge de voz (websocket_bridge.py) a
proposito: si el panel se cae, la voz sigue funcionando.

    uvicorn panel_api:app --host 0.0.0.0 --port 8766

Autenticacion: token unico en PANEL_TOKEN (o servidor/.env). Sin PANEL_TOKEN
puesto, el panel NO arranca -- mejor fallar alto que quedar abierto por
descuido en una IP publica.
"""
import asyncio
import copy
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Optional, Union

import websockets
import yaml
from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

AQUI = Path(__file__).parent
sys.path.insert(0, str(AQUI))

from nucleo.entorno import carga_env, lee_todas, escribe, ENV_PATH  # noqa: E402

carga_env()

import panel  # noqa: E402  (reusa _config, claves_estado, guarda_proveedores, _disponible...)
import mcps_cli  # noqa: E402  (reusa carga, guarda, estado, alterna, probar)
from sdk_mcp.generador import crear_mcp, MCPExistente  # noqa: E402

TOKEN = os.getenv("PANEL_TOKEN")

app = FastAPI(title="Panel — Asistente ESP32", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # detras de nginx con TLS; endurecer si se expone distinto
    allow_methods=["*"],
    allow_headers=["*"],
)


def _requiere_token(authorization: Optional[str] = Header(None)):
    if not TOKEN:
        raise HTTPException(500, "PANEL_TOKEN no esta puesto en el servidor")
    esperado = f"Bearer {TOKEN}"
    if not authorization or not secrets.compare_digest(authorization, esperado):
        raise HTTPException(401, "token invalido")


router_dep = [Depends(_requiere_token)]


# ================================================================
#  Estado general
# ================================================================
@app.get("/api/salud")
def salud():
    """Sin auth: para que nginx/systemd puedan comprobar que el proceso vive."""
    return {"ok": True}


@app.get("/api/estado", dependencies=router_dep)
async def estado():
    from proveedores import cadenas_desde_config

    cfg = panel._config()
    cadenas = cadenas_desde_config(cfg)
    proveedores_activos = {
        cap: (cadena.activo.nombre if cadena.activo else None) for cap, cadena in cadenas.items()
    }
    _, cfg_mcp, activos = mcps_cli.listar_silencioso()
    return {
        "proveedores_activos": proveedores_activos,
        "mcp_activos": activos,
        "mcp_total": len(mcps_cli.carga()[0]),
        "claves_faltantes": [f["clave"] for f in panel.claves_estado() if not f["puesta"]],
    }


# ================================================================
#  Claves de API
# ================================================================
@app.get("/api/claves", dependencies=router_dep)
def claves():
    return panel.claves_estado()


class ClaveIn(BaseModel):
    valor: str


@app.put("/api/claves/{nombre}", dependencies=router_dep)
def poner_clave(nombre: str, body: ClaveIn):
    actuales = lee_todas()
    actuales[nombre] = body.valor
    escribe(actuales)
    os.environ[nombre] = body.valor
    return {"ok": True}


@app.delete("/api/claves/{nombre}", dependencies=router_dep)
def borrar_clave(nombre: str):
    actuales = lee_todas()
    actuales.pop(nombre, None)
    escribe(actuales)
    return {"ok": True}


# ================================================================
#  Proveedores (LLM / STT / TTS)
# ================================================================
@app.get("/api/proveedores", dependencies=router_dep)
def proveedores():
    cfg = panel._config()
    prov = cfg.get("proveedores", {})
    catalogo = cfg.get("catalogo", {})
    out = {}
    for cap in ("llm", "stt", "tts"):
        orden = prov.get(cap, [])
        disponibles = []
        for nombre, datos in (catalogo.get(cap) or {}).items():
            ok, motivo = panel._disponible(cap, nombre, cfg)
            disponibles.append({
                "nombre": nombre,
                "descripcion": datos.get("description", ""),
                "listo": ok,
                "motivo": motivo,
                "activo": bool(orden) and orden[0] == nombre,
            })
        out[cap] = {"orden": orden, "catalogo": disponibles}
    return out


class OrdenIn(BaseModel):
    orden: list[str]


@app.put("/api/proveedores/{capacidad}", dependencies=router_dep)
def reordenar_proveedores(capacidad: str, body: OrdenIn):
    if capacidad not in ("llm", "stt", "tts"):
        raise HTTPException(400, "capacidad debe ser llm, stt o tts")
    cfg = panel._config()
    disponibles = list((cfg.get("catalogo", {}).get(capacidad) or {}).keys())
    desconocidos = [x for x in body.orden if x not in disponibles]
    if desconocidos:
        raise HTTPException(400, f"no estan en el catalogo: {', '.join(desconocidos)}")
    cfg.setdefault("proveedores", {})[capacidad] = body.orden
    panel.guarda_proveedores(cfg)
    return {"ok": True, "orden": body.orden}


# ================================================================
#  MCP
# ================================================================
@app.get("/api/mcp", dependencies=router_dep)
def mcp_listado():
    catalogo, _, activos = mcps_cli.listar_silencioso()
    filas = []
    for nombre, entrada in sorted(catalogo.items(), key=lambda kv: (kv[1].get("categoria", ""), kv[0])):
        filas.append({
            "nombre": nombre,
            "categoria": entrada.get("categoria", "otros"),
            "descripcion": entrada.get("descripcion", ""),
            "requisitos": entrada.get("requisitos", ""),
            "activo": nombre in activos,
            "falta_binario": mcps_cli.falta_binario(entrada.get("command", [""])) if entrada.get("command") else False,
            "env_pendiente": mcps_cli.env_pendiente(entrada),
        })
    return filas


@app.post("/api/mcp/{nombre}/activar", dependencies=router_dep)
def mcp_activar(nombre: str):
    return _alterna_por_nombre(nombre, quiere_activo=True)


@app.post("/api/mcp/{nombre}/desactivar", dependencies=router_dep)
def mcp_desactivar(nombre: str):
    return _alterna_por_nombre(nombre, quiere_activo=False)


def _alterna_por_nombre(nombre: str, quiere_activo: bool):
    cat, cfg = mcps_cli.carga()
    if nombre not in cat:
        raise HTTPException(404, f"'{nombre}' no esta en el catalogo")
    orden = sorted(cat.items(), key=lambda kv: (kv[1].get("categoria", ""), kv[0]))
    posicion = next(i for i, (n, _) in enumerate(orden, 1) if n == nombre)
    ya_activo = nombre in (cfg.get("tools_to_enable") or [])
    if ya_activo != quiere_activo:
        mcps_cli.alterna(posicion, orden, cfg)
    return {"ok": True, "nombre": nombre, "activo": quiere_activo, "reinicio_pendiente": True}


class MCPNuevoIn(BaseModel):
    nombre: str
    descripcion: str
    categoria: str = "local"
    activar: bool = True


@app.post("/api/mcp/nuevo", dependencies=router_dep)
def mcp_nuevo(body: MCPNuevoIn):
    try:
        ruta = crear_mcp(
            nombre=body.nombre,
            descripcion=body.descripcion,
            categoria=body.categoria,
            activar=body.activar,
        )
    except MCPExistente as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "archivo": str(ruta), "reinicio_pendiente": True}


@app.get("/api/mcp/probar", dependencies=router_dep)
async def mcp_probar():
    """Arranca cada MCP activo (proceso aparte, no toca el bridge de voz que
    ya este corriendo) y lista sus herramientas -- version HTTP de
    'mcps_cli.py --probar'."""
    from nucleo.mcp_pool import MCPPool

    pool = MCPPool(str(mcps_cli.CONFIG))
    await pool.connect_all()
    por_servidor: dict[str, list[str]] = {}
    for nombre, h in pool.herramientas.items():
        por_servidor.setdefault(h["servidor"], []).append(nombre)
    await pool.cerrar()
    return por_servidor


# ================================================================
#  Control del servicio de voz (systemd) -- ver Fase 3 en tasks.md.
#  Reiniciar es el "aplica en caliente" honesto para lo que aun no soporta
#  hot-reload real dentro del pool (ver 001-panel-administracion-mcp/tasks.md,
#  Fase 0). Requiere que el usuario del panel tenga sudo sin password para
#  `systemctl restart agentic-voz`, configurado aparte -- no lo hace este
#  archivo.
# ================================================================
@app.post("/api/servicio-voz/reiniciar", dependencies=router_dep)
def reiniciar_servicio_voz():
    import subprocess

    try:
        subprocess.run(
            ["sudo", "-n", "systemctl", "restart", "agentic-voz"],
            check=True, capture_output=True, timeout=15, text=True,
        )
    except FileNotFoundError:
        raise HTTPException(501, "systemctl no disponible en este host")
    except subprocess.CalledProcessError as e:
        raise HTTPException(500, f"fallo al reiniciar: {e.stderr or e}")
    return {"ok": True}


# ================================================================
#  Ajustes generales del panel — apariencia, dispositivo, pantallas,
#  clima/noticias y agentes. Guardado real en config.yaml bajo la clave
#  'ajustes' (mismo patron que guarda_proveedores: se reescribe solo ese
#  bloque para no perder los comentarios del resto del fichero).
#
#  OJO: 'dispositivo' y 'pantallas' guardan la INTENCION del usuario ya
#  mismo, de verdad, en disco. Que el ESP32 la aplique (brillo, volumen,
#  activar/desactivar una pantalla en caliente) depende de que el firmware
#  entienda un mensaje nuevo por el protocolo v2 (ver nucleo/canal.py) --
#  eso todavia no existe, asi que hoy actua como "se guarda para la
#  proxima sincronizacion", no como control remoto instantaneo del hardware.
# ================================================================
AJUSTES_DEFAULT = {
    "apariencia": {
        "tema": "glass-dark", "acento": "#5ee6c9", "acento2": "#7aa2ff",
        "logo": "", "fondo": "", "nombre_asistente": "Asistente ESP32",
    },
    "dispositivo": {"brillo": 80, "volumen": 70, "tema_hud": "cyan", "efectos": True},
    # Los 'id' y su ORDEN son el contrato con el firmware: el indice de cada
    # entrada aqui es el valor de hud_screen_t en components/hud/include/hud.h
    # (y de Canal.PANTALLAS). El campo 'nombre' es solo etiqueta para el panel;
    # 'titulo_hud' es lo que el propio dispositivo pinta en la cabecera.
    "pantallas": [
        {"id": "nucleo",   "nombre": "Núcleo",         "titulo_hud": "NUCLEO",   "activa": True, "orden": 0},
        {"id": "reloj",    "nombre": "Reloj",          "titulo_hud": "CRONO",    "activa": True, "orden": 1},
        {"id": "clima",    "nombre": "Clima",          "titulo_hud": "ATMOS",    "activa": True, "orden": 2},
        {"id": "voz",      "nombre": "Voz",            "titulo_hud": "VOZ",      "activa": True, "orden": 3},
        {"id": "chat",     "nombre": "Conversación",   "titulo_hud": "REGISTRO", "activa": True, "orden": 4},
        {"id": "noticias", "nombre": "Noticias IA",    "titulo_hud": "SENALES",  "activa": True, "orden": 5},
        {"id": "mac",      "nombre": "Telemetría Mac", "titulo_hud": "MAQUINA",  "activa": True, "orden": 6},
        {"id": "creativo", "nombre": "Unity/Blender",  "titulo_hud": "FORJA",    "activa": True, "orden": 7},
        {"id": "ajustes",  "nombre": "Ajustes",        "titulo_hud": "AJUSTES",  "activa": True, "orden": 8},
        {"id": "sistema",  "nombre": "Diagnóstico",    "titulo_hud": "DIAG",     "activa": True, "orden": 9},
    ],
    "clima": {"proveedor": "open-meteo", "ciudad": "", "leer_tts": False},
    "noticias": {"fuente": "hnrss+techcrunch+ainews", "leer_tts": True},
    "tts_leer_respuestas": True,
    "agentes": [],
}


def _lee_ajustes() -> dict:
    cfg = panel._config()
    guardado = cfg.get("ajustes") or {}
    base = copy.deepcopy(AJUSTES_DEFAULT)
    out = {}
    for k, v in base.items():
        if k in guardado:
            out[k] = {**v, **guardado[k]} if isinstance(v, dict) and isinstance(guardado[k], dict) else guardado[k]
        else:
            out[k] = v
    return out


def _guarda_ajustes(ajustes: dict):
    ruta = panel.CONFIG_PATH
    texto = ruta.read_text()
    bloque = yaml.safe_dump({"ajustes": ajustes}, allow_unicode=True, sort_keys=False)
    if re.search(r"\najustes:", texto):
        texto = re.sub(r"\najustes:.*\Z", "\n" + bloque, texto, flags=re.S)
    else:
        texto = texto.rstrip("\n") + "\n\n" + bloque
    ruta.write_text(texto)


@app.get("/api/ajustes", dependencies=router_dep)
def ajustes_get():
    return _lee_ajustes()


class AjustesIn(BaseModel):
    seccion: str
    valores: Union[dict, list]


@app.put("/api/ajustes", dependencies=router_dep)
def ajustes_put(body: AjustesIn):
    actuales = _lee_ajustes()
    if body.seccion not in actuales:
        raise HTTPException(400, f"seccion desconocida: {body.seccion}")
    actuales[body.seccion] = body.valores
    _guarda_ajustes(actuales)
    return {"ok": True, "ajustes": actuales}


# ================================================================
#  Control en vivo del ESP32 — brillo, volumen, tema, efectos, reinicio.
#
#  panel_api.py corre como proceso APARTE del bridge de voz
#  (websocket_bridge.py), asi que no comparte el objeto CANAL en memoria.
#  Se conecta como cliente de control por websocket, igual que hace
#  mcps/dispositivo.py -- mismo protocolo, misma guardia de permisos y
#  limite de tasa (ver nucleo/guardia.py, capacidad 'administrar').
#  Requiere firmware con el manejador de 'config'/'reiniciar' en
#  components/voice/voice.c; con un firmware viejo el mensaje se ignora
#  sin romper nada, pero tampoco aplica el cambio.
# ================================================================
HUD_BRIDGE_LOCAL = os.getenv("HUD_BRIDGE", "ws://127.0.0.1:8765")


async def _control_llama(fn: str, args: dict, timeout: float = 8):
    async def _hazlo():
        async with websockets.connect(HUD_BRIDGE_LOCAL, open_timeout=5) as ws:
            await ws.send(json.dumps({"t": "hola", "rol": "control"}))
            await ws.send(json.dumps({"t": "cmd", "rid": 1, "fn": fn, "args": args}))
            async for msg in ws:
                if isinstance(msg, bytes):
                    continue
                d = json.loads(msg)
                if d.get("t") == "res":
                    return d.get("v")
    try:
        return await asyncio.wait_for(_hazlo(), timeout=timeout)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"no se pudo hablar con el bridge de voz ({HUD_BRIDGE_LOCAL}): {e}")


class DispositivoConfigIn(BaseModel):
    brillo: Optional[int] = None
    volumen: Optional[int] = None
    tema_hud: Optional[str] = None
    efectos: Optional[bool] = None


@app.post("/api/dispositivo/aplicar", dependencies=router_dep)
async def dispositivo_aplicar(body: DispositivoConfigIn):
    args = {k: v for k, v in body.dict().items() if v is not None}
    if not args:
        raise HTTPException(400, "nada que aplicar")
    v = await _control_llama("configurar", args)
    if isinstance(v, dict) and v.get("error"):
        raise HTTPException(400, v["error"])
    # Se guarda tambien en ajustes.dispositivo para que el panel recuerde
    # lo ultimo pedido, independientemente de si el firmware lo aplico.
    actuales = _lee_ajustes()
    actuales["dispositivo"].update(args)
    _guarda_ajustes(actuales)
    return {"ok": True, "resultado": v}


@app.post("/api/dispositivo/pantallas", dependencies=router_dep)
async def dispositivo_pantallas():
    """Empuja al ESP32 el carrusel guardado en ajustes.pantallas.

    Se lee de config.yaml en vez de recibirlo en el cuerpo a proposito: el
    panel guarda primero (PUT /api/ajustes) y luego pide aplicar, asi que lo
    que llega al dispositivo es siempre lo que quedo persistido -- no hay
    forma de que la pantalla muestre una cosa y el ESP32 tenga otra.
    """
    pantallas = _lee_ajustes()["pantallas"]
    en_orden = sorted(pantallas, key=lambda p: p.get("orden", 99))
    activas = [p["id"] for p in en_orden if p.get("activa")]
    orden = [p["id"] for p in en_orden]
    if not activas:
        raise HTTPException(400, "no puedes dejar el carrusel sin ninguna pantalla")
    v = await _control_llama("pantallas", {"activas": activas, "orden": orden})
    if isinstance(v, dict) and v.get("error"):
        raise HTTPException(400, v["error"])
    return {"ok": True, "resultado": v}


@app.post("/api/dispositivo/reiniciar", dependencies=router_dep)
async def dispositivo_reiniciar():
    v = await _control_llama("reiniciar", {})
    if isinstance(v, dict) and v.get("error"):
        raise HTTPException(400, v["error"])
    return {"ok": True, "resultado": v}


# ================================================================
#  Frontend estatico (panel_web/)
# ================================================================
WEB = AQUI / "panel_web"
if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB), name="assets")

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")
