"""
Pendientes del dia -- cerebro-jornada (MISION.md §2a, "el parte del dia").

Por que un JSON local y no un calendario de verdad
---------------------------------------------------
Outlook se retiro de la mision a proposito: el correo es un pozo de
atencion, justo lo contrario de lo que este aparato existe para proteger
(MISION.md §2a). Sin Outlook no hay otra fuente de agenda corporativa
conectada todavia -- y en vez de fabricar una integracion a medias con un
proveedor que nadie pidio, esto resuelve la necesidad real de HOY: una
lista de pendientes que el agente y Mario pueden tocar por voz o por texto,
con una hora opcional que la convierte tambien en un horario ligero del
dia. El dia que haya un calendario real conectado, esto sigue siendo util
para lo que un calendario no cubre: "me acorde de esto a media mañana".

DISEÑO: mismo patron que nucleo/entorno.py -- un fichero de texto plano
(aqui JSON) que vive junto al servidor, se lee y se reescribe entero. Sin
base de datos: son decenas de items, no miles.
"""
import json, logging, time
from pathlib import Path

log = logging.getLogger("pendientes")

AQUI = Path(__file__).resolve().parent.parent   # server/brain/
RUTA = AQUI / "pendientes.json"

# RNF-3 de superpower, mismo espiritu aqui: un proceso que corre semanas no
# puede acumular pendientes completados para siempre.
TOPE = 200


def _lee() -> list[dict]:
    if not RUTA.exists():
        return []
    try:
        return json.loads(RUTA.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("pendientes.json ilegible, se trata como vacio: %s", e)
        return []


def _escribe(items: list[dict]):
    # Los completados viejos se podan ANTES de escribir, no al leer: asi el
    # fichero en disco nunca crece sin limite aunque nadie llame a limpiar().
    activos = [p for p in items if not p.get("hecho")]
    completados = [p for p in items if p.get("hecho")]
    completados.sort(key=lambda p: p.get("hecho_ts", 0), reverse=True)
    items = activos + completados[: max(0, TOPE - len(activos))]
    RUTA.write_text(json.dumps(items, ensure_ascii=False, indent=2))


def agrega(texto: str, hora: str = "") -> dict:
    """Añade un pendiente. 'hora' es opcional y en texto libre ('09:30',
    'antes del almuerzo'): esto no es un calendario con recordatorios
    disparados por reloj, es una lista ordenada que un humano lee."""
    items = _lee()
    item = {
        "id": (max((p["id"] for p in items), default=0) + 1),
        "texto": str(texto).strip(),
        "hora": str(hora).strip(),
        "creado_ts": time.time(),
        "hecho": False,
        "hecho_ts": None,
    }
    items.append(item)
    _escribe(items)
    return item


def completa(id: int) -> dict | None:
    items = _lee()
    for p in items:
        if p["id"] == int(id):
            p["hecho"] = True
            p["hecho_ts"] = time.time()
            _escribe(items)
            return p
    return None


def lista(incluir_hechos: bool = False) -> list[dict]:
    items = _lee()
    if not incluir_hechos:
        items = [p for p in items if not p.get("hecho")]
    # Los que tienen hora van primero y ordenados; los sin hora, al final en
    # el orden en que se crearon -- es como se lee un dia normal: primero lo
    # citado, despues lo suelto.
    con_hora = sorted((p for p in items if p["hora"]), key=lambda p: p["hora"])
    sin_hora = [p for p in items if not p["hora"]]
    return con_hora + sin_hora


def pendientes_de_ayer() -> list[dict]:
    """Los que quedaron sin completar y no son de hoy -- RF de 'parte del
    dia': qué quedó pendiente de ayer (MISION.md §2a)."""
    hoy = time.strftime("%Y-%m-%d")
    return [p for p in lista() if time.strftime("%Y-%m-%d", time.localtime(p["creado_ts"])) != hoy]
