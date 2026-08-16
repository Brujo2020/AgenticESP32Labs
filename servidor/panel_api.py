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
import os
import secrets
import sys
from pathlib import Path
from typing import Optional

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
#  Frontend estatico (panel_web/)
# ================================================================
WEB = AQUI / "panel_web"
if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB), name="assets")

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")
