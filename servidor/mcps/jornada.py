#!/usr/bin/env python3
"""
MCP 'jornada' -- el parte del dia, cerebro-jornada (MISION.md §2a).

"Un solo barrido, no seis apps": pendientes, clima y tendencias en una sola
llamada, mas los pendientes sueltos para tocarlos por voz. Vive en proceso
(no habla con el puente como dispositivo.py/foco.py) porque no necesita
ningun Canal ni al MOTOR: pendientes.py es un fichero local y clima/noticias
son HTTP directo, igual que hacen mcps/clima.py y noticias.py.

Sin Outlook -- retirado de la mision a proposito (MISION.md §2a): el correo
es un pozo de atencion. Ver nucleo/pendientes.py para el porque de una lista
local en vez de un calendario externo.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sdk_mcp import MCPBase
from nucleo import pendientes as P
from noticias import titulares

m = MCPBase("jornada")

# Mismas coordenadas que el bloque de alertas de websocket_bridge.py
# (ALERTA_LAT/ALERTA_LON) -- si un dia se hacen configurables desde el
# panel, deben cambiar juntas.
LAT, LON = -33.45, -70.66   # Santiago de Chile


async def _clima_hoy() -> str:
    try:
        import httpx
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
               "&current=temperature_2m,weather_code"
               "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
               "&timezone=auto&forecast_days=1")
        async with httpx.AsyncClient(timeout=10) as c:
            d = (await c.get(url)).json()
        cur = d.get("current", {})
        dia = d.get("daily", {})
        t = cur.get("temperature_2m")
        tmax = (dia.get("temperature_2m_max") or [None])[0]
        tmin = (dia.get("temperature_2m_min") or [None])[0]
        lluvia = (dia.get("precipitation_probability_max") or [0])[0]
        partes = []
        if t is not None:
            partes.append(f"{t:.0f}°C ahora")
        if tmin is not None and tmax is not None:
            partes.append(f"minima {tmin:.0f}° maxima {tmax:.0f}°")
        if lluvia:
            partes.append(f"{lluvia:.0f}% de lluvia")
        return ", ".join(partes) if partes else "sin datos de clima"
    except Exception as e:
        return f"clima no disponible ({e})"


@m.tool()
async def parte_del_dia() -> dict:
    """El barrido completo de la mañana: pendientes de hoy, lo que quedo
    de ayer sin cerrar, clima, y titulares de tendencias (IA, 3D con IA,
    Unity, modelos chinos open source).

    Pensado para UNA sola llamada al empezar el dia -- "un solo barrido, no
    seis apps" (MISION.md §2a). Sin correo: se retiro de la mision a
    proposito.
    """
    hoy = P.lista()
    ayer = P.pendientes_de_ayer()
    clima = await _clima_hoy()
    try:
        ts = await titulares(5)
    except Exception:
        ts = []
    return {
        "pendientes_hoy": [f"{p['hora'] + ' — ' if p['hora'] else ''}{p['texto']}"
                            for p in hoy if p not in ayer],
        "pendientes_de_ayer_sin_cerrar": [p["texto"] for p in ayer],
        "clima": clima,
        "tendencias": ts,
    }


@m.tool()
async def pendiente_agrega(texto: str, hora: str = "") -> dict:
    """Añade un pendiente al dia. 'hora' es texto libre y opcional
    ('09:30', 'antes del almuerzo', o vacio para 'sin hora fija')."""
    return P.agrega(texto, hora)


@m.tool()
async def pendiente_completa(id: int) -> dict:
    """Marca un pendiente como hecho, por su id (el que devuelve
    pendiente_lista)."""
    r = P.completa(id)
    return r or {"error": f"no existe el pendiente {id}"}


@m.tool()
async def pendiente_lista(incluir_hechos: bool = False) -> list[dict]:
    """Lista los pendientes, ordenados por hora primero y sin hora despues.
    Con incluir_hechos=True tambien trae los ya completados."""
    return P.lista(incluir_hechos)


if __name__ == "__main__":
    m.run()
