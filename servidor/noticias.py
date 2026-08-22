"""
Titulares de IA por RSS. Sin API key ni dependencias raras.
Se cortan a lo que cabe en la pantalla circular del ESP32.
"""
import asyncio, re, html, logging
import httpx

log = logging.getLogger("noticias")

FUENTES = [
    # DEMO (18 ago 2026): noticias relevantes para la presentacion en el
    # trabajo -- Google News RSS es el formato mas confiable para esto
    # porque no depende de que un sitio puntual tenga su propio feed, se
    # arma con cualquier busqueda. Los feeds genericos de IA quedan
    # despues como respaldo.
    "https://news.google.com/rss/search?q=NTT+DATA&hl=es-419&gl=CL&ceid=CL:es",
    "https://news.google.com/rss/search?q=tecnologia+Chile&hl=es-419&gl=CL&ceid=CL:es",
    "https://hnrss.org/newest?q=AI+OR+LLM&count=10",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.artificialintelligence-news.com/feed/",
]

ANCHO = 33          # caracteres que caben en una linea del HUD

# Google News, TechCrunch y hnrss responden 403 a clientes sin User-Agent de
# navegador (httpx manda "python-httpx/x.y" por defecto). No es rate limit ni
# bloqueo de IP: con esta cabecera contestan normal. Sin esto, 'titulares'
# devolvia lista vacia y el HUD se quedaba sin noticias sin decir por que.
CABECERAS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


def _recorta(t: str) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
    t = re.sub(r"\s+", " ", t).upper()          # la fuente del HUD es mayusculas
    return t[:ANCHO - 1] + "." if len(t) > ANCHO else t


async def titulares_detallado(maximo: int = 5) -> dict:
    """Como titulares(), pero ademas devuelve por que fallo cada fuente.

    Existe porque el modo silencioso (solo log.warning) hacia imposible
    diagnosticar desde el panel: se veia "no hay titulares" sin distinguir
    entre "no hay internet", "403 del feed" o "el feed cambio de formato".
    """
    salida: list[str] = []
    errores: list[str] = []
    async with httpx.AsyncClient(timeout=10, follow_redirects=True,
                                 headers=CABECERAS) as c:
        for url in FUENTES:
            if len(salida) >= maximo:
                break
            try:
                r = await c.get(url)
                r.raise_for_status()
                for m in re.finditer(r"<title>(.*?)</title>", r.text, re.S):
                    t = _recorta(m.group(1))
                    # el primer <title> del feed es el nombre del medio
                    if len(t) > 12 and t not in salida:
                        salida.append(t)
                    if len(salida) >= maximo:
                        break
            except Exception as e:
                corta = url.split("//", 1)[-1].split("/")[0]
                errores.append(f"{corta}: {e}")
                log.warning("fuente %s fallo: %s", url, e)
    return {"titulares": salida[:maximo], "errores": errores}


async def titulares(maximo: int = 5) -> list[str]:
    return (await titulares_detallado(maximo))["titulares"]


if __name__ == "__main__":
    d = asyncio.run(titulares_detallado())
    for t in d["titulares"]:
        print("-", t)
    for e in d["errores"]:
        print("  (fallo)", e)
