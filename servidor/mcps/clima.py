try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("❌ pip install mcp")
    raise

mcp = FastMCP("clima")

@mcp.tool()
async def clima_actual(latitud: float, longitud: float) -> dict:
    try:
        import aiohttp
    except ImportError:
        return {"error": "pip install aiohttp"}

    async with aiohttp.ClientSession() as session:
        try:
            url = f"https://api.open-meteo.com/v1/forecast?latitude={latitud}&longitude={longitud}&current=temperature_2m,relative_humidity_2m,weather_code&timezone=auto"
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
        except Exception as e:
            return {"error": str(e)}

@mcp.tool()
async def clima_ubicacion(ciudad: str) -> dict:
    try:
        import aiohttp
    except ImportError:
        return {"error": "pip install aiohttp"}

    async with aiohttp.ClientSession() as session:
        try:
            url = f"https://geocoding-api.open-meteo.com/v1/search?name={ciudad}&count=1"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                results = data.get("results", [])
                if not results:
                    return {"error": f"No: {ciudad}"}
                r = results[0]
                return {"ciudad": r.get("name"), "lat": r.get("latitude"), "lon": r.get("longitude")}
        except Exception as e:
            return {"error": str(e)}

if __name__ == "__main__":
    mcp.run(transport="stdio")
