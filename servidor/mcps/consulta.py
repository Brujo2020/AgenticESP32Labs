"""
MCP: consulta — trae cualquier JSON de una URL publica y se lo entrega al
agente para que lo muestre en el HUD (con la tool 'hud_mostrar' que ya
tiene el agente, no hace falta nada mas aqui).

DEMO (18 ago 2026): pensado para el "pide cualquier JSON y te lo muestro"
que se armo rapido para la presentacion. Sin API key: solo GET publico.
Mismo patron que clima.py (MCPBase + @m.tool()).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sdk_mcp import MCPBase

m = MCPBase("consulta")

# Limite de seguridad: una respuesta gigante no cabe en la pantalla del HUD
# y satura el contexto del LLM. Se recorta antes de devolver, no despues.
MAX_CARACTERES = 2000


@m.tool()
async def consultar_json(url: str) -> dict:
    """Trae el JSON de una URL publica (GET, sin autenticacion) y lo
    devuelve para que el agente decida como mostrarlo (por ejemplo con
    hud_mostrar). Util para demos: 'muestrame el JSON de tal API'."""
    import aiohttp

    if not url.startswith(("http://", "https://")):
        return {"error": "la url debe empezar con http:// o https://"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                texto = await resp.text()
                if resp.status != 200:
                    return {"error": f"API: {resp.status}", "cuerpo": texto[:MAX_CARACTERES]}
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    # No todos los endpoints declaran content-type JSON
                    # correctamente -- se intenta igual como texto crudo
                    # en vez de fallar por eso.
                    return {"texto": texto[:MAX_CARACTERES]}
                return {"json": data} if len(str(data)) <= MAX_CARACTERES else {
                    "json_recortado": str(data)[:MAX_CARACTERES] + "...",
                    "aviso": "respuesta muy larga, se recorto",
                }
    except Exception as e:
        return {"error": str(e) or type(e).__name__}


if __name__ == "__main__":
    m.run()
