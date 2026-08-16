"""
MCP: clima — Open-Meteo, sin API key.

Migrado a sdk_mcp.MCPBase (spec 002-sdk-mcp-scaffolding) como prueba de que
la capa comun no cambia el comportamiento externo: mismas tools, mismos
nombres, mismas respuestas. MCPBase ya envuelve el try/except homogeneo, asi
que las tools no necesitan atrapar sus propios errores de red.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sdk_mcp import MCPBase

m = MCPBase("clima")


@m.tool()
async def clima_actual(latitud: float, longitud: float) -> dict:
    import aiohttp

    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={latitud}&longitude={longitud}"
        "&current=temperature_2m,relative_humidity_2m,weather_code&timezone=auto"
    )
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return {"error": f"API: {resp.status}"}
            data = await resp.json()
            current = data.get("current", {})
            return {
                "temperatura": current.get("temperature_2m"),
                "unidad": current.get("temperature_2m_unit", "°C"),
                "humedad": current.get("relative_humidity_2m"),
            }


@m.tool()
async def clima_ubicacion(ciudad: str) -> dict:
    import aiohttp

    url = f"https://geocoding-api.open-meteo.com/v1/search?name={ciudad}&count=1"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()
            resultados = data.get("results", [])
            if not resultados:
                return {"error": f"No: {ciudad}"}
            r = resultados[0]
            return {"ciudad": r.get("name"), "lat": r.get("latitude"), "lon": r.get("longitude")}


if __name__ == "__main__":
    m.run()
