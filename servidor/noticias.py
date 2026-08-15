"""
Titulares de IA por RSS. Sin API key ni dependencias raras.
Se cortan a lo que cabe en la pantalla circular del ESP32.
"""
import asyncio, re, html, logging
import httpx

log = logging.getLogger("noticias")

FUENTES = [
    "https://hnrss.org/newest?q=AI+OR+LLM&count=10",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.artificialintelligence-news.com/feed/",
]

ANCHO = 33          # caracteres que caben en una linea del HUD


def _recorta(t: str) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
    t = re.sub(r"\s+", " ", t).upper()          # la fuente del HUD es mayusculas
    return t[:ANCHO - 1] + "." if len(t) > ANCHO else t


async def titulares(maximo: int = 5) -> list[str]:
    salida: list[str] = []
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
        for url in FUENTES:
            if len(salida) >= maximo:
                break
            try:
                r = await c.get(url)
                r.raise_for_status()
                for m in re.finditer(r"<title>(.*?)</title>", r.text, re.S)  :
                    t = _recorta(m.group(1))
                    # el primer <title> del feed es el nombre del medio
                    if len(t) > 12 and t not in salida:
                        salida.append(t)
                    if len(salida) >= maximo:
                        break
            except Exception as e:
                log.warning("fuente %s fallo: %s", url, e)
    return salida[:maximo]


if __name__ == "__main__":
    for t in asyncio.run(titulares()):
        print("-", t)
