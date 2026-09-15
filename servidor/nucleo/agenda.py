"""
Agenda — los bloques anclados que Ritmo no puede inventar (ola `agenda`, ver
.kiro/specs/agenda/{requirements,design}.md).

Ingesta por empuje (RF-1): este modulo nunca sale a buscar un calendario a
ningun sitio, solo guarda lo que alguien (un Atajo de iPhone, `curl`, lo que
sea) le entrega por `POST /api/agenda` (panel_api.py). Sin credenciales de
nadie, no hay nada que filtrar.

DISEÑO: mismo patron que nucleo/pendientes.py -- un JSON local junto al
servidor, se lee y se reescribe entero. La diferencia real es RF-2: aqui el
filtrado de campos es una garantia de diseño, no de conveniencia -- ver
_solo_campos_permitidos().
"""
import json, logging, time
from pathlib import Path

log = logging.getLogger("agenda")

AQUI = Path(__file__).resolve().parent.parent   # server/brain/
RUTA = AQUI / "agenda.json"

# RF-2: esto es TODO lo que un evento puede llevar. Cualquier otro campo
# (invitados, cuerpo, enlaces de reunion, correos) se descarta AQUI, al
# guardar -- no es una convencion de que "el Atajo no deberia mandarlo", es
# que aunque lo mande, no sobrevive a _solo_campos_permitidos().
CAMPOS_EVENTO = {"inicio", "fin", "titulo", "calendario", "organizador", "modo"}
CALENDARIOS_VALIDOS = {"trabajo", "personal"}

# RF-4: ventana de vigencia y tamaño razonable. Esto alimenta el dia, no es
# un archivo historico (RF-5) -- fuera de esta ventana se rechaza entero.
VENTANA_DIAS = 2
MAX_EVENTOS_POR_POST = 200

# D-2: si el 'modo' que manda el cliente no es uno de los de verdad
# (nucleo/ritmo.py:MODOS), se recalcula con la regla de respaldo de RF-3 en
# vez de guardar basura o reventar. Duplicado deliberado del set de modos
# (importar nucleo.ritmo aqui crearia un ciclo agenda->ritmo->planificador->
# agenda si planificador importara agenda a nivel de modulo; se evita
# importando agenda.py solo dentro de la funcion que lo necesita, ver
# planificador.py::agenda_anclados_extra()). Si nucleo/ritmo.py añade un
# modo nuevo, hay que añadirlo aqui tambien -- comprobado por
# pruebas/test_agenda.py contra el set real de ritmo.py.
MODOS_VALIDOS = {"material", "oferta", "investigacion", "construccion",
                  "arquitectura", "estudio", "multitarea", "reunion", "descanso"}


class AgendaInvalida(ValueError):
    """Lo que panel_api.py traduce a un 400 con el motivo exacto (RF-4:
    'rechaza... y no modifica nada' -- por eso esto se lanza ANTES de tocar
    el fichero, nunca a medio guardar)."""


def _clasifica_respaldo(ev: dict) -> str:
    """RF-3, regla de respaldo cuando no hay modelo on-device (o el modo que
    llego no es valido): invitados => reunion; trabajo sin invitados =>
    material; resto => multitarea.

    Limitacion conocida (D-2 en design.md): RF-2 no manda la lista de
    invitados a proposito, asi que "con invitados" se aproxima con
    organizador=False como señal de "probablemente hay mas gente". Imperfecta,
    nunca peor que no tener agenda.
    """
    con_invitados_probable = ev.get("organizador") is False
    if con_invitados_probable:
        return "reunion"
    if ev.get("calendario") == "trabajo":
        return "material"
    return "multitarea"


def _valida_evento(ev: dict, ahora: float) -> dict:
    if not isinstance(ev, dict):
        raise AgendaInvalida("cada evento debe ser un objeto")
    inicio, fin = ev.get("inicio"), ev.get("fin")
    titulo = str(ev.get("titulo", "")).strip()
    calendario = ev.get("calendario")
    if not inicio or not fin:
        raise AgendaInvalida(f"evento sin 'inicio'/'fin': {ev!r}")
    if not titulo:
        raise AgendaInvalida("evento sin 'titulo'")
    if calendario not in CALENDARIOS_VALIDOS:
        raise AgendaInvalida(f"'calendario' debe ser trabajo|personal, llego: {calendario!r}")
    try:
        ts_inicio = time.mktime(time.strptime(inicio[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        raise AgendaInvalida(f"'inicio' no es ISO 8601 valido: {inicio!r}")
    if abs(ts_inicio - ahora) > VENTANA_DIAS * 86400:
        raise AgendaInvalida(f"evento fuera de la ventana de ±{VENTANA_DIAS} dias: {titulo!r}")
    modo = ev.get("modo")
    if modo not in MODOS_VALIDOS:
        modo = _clasifica_respaldo(ev)
    # RF-2: solo estos campos sobreviven, en este orden estable.
    return {
        "inicio": str(inicio), "fin": str(fin), "titulo": titulo[:80],
        "calendario": calendario, "organizador": bool(ev.get("organizador", True)),
        "modo": modo,
    }


def guarda(eventos: list, ts: float = None) -> dict:
    """Valida y guarda un lote de eventos, agrupados por dia (campo 'inicio').
    Reemplaza CADA DIA que aparezca en el lote (D-1) -- un POST que solo trae
    eventos de hoy no toca lo guardado de mañana, y viceversa.

    Lanza AgendaInvalida sin escribir nada si CUALQUIER evento del lote es
    invalido (RF-4, criterio de aceptacion 3: "no modifica nada") -- validar
    TODO antes de guardar NADA.
    """
    ahora = ts if ts is not None else time.time()
    if not isinstance(eventos, list):
        raise AgendaInvalida("'eventos' debe ser una lista")
    if len(eventos) > MAX_EVENTOS_POR_POST:
        raise AgendaInvalida(f"demasiados eventos en un POST (max {MAX_EVENTOS_POR_POST})")
    validados = [_valida_evento(ev, ahora) for ev in eventos]

    datos = _lee()
    por_dia = {}
    for ev in validados:
        fecha = ev["inicio"][:10]
        por_dia.setdefault(fecha, []).append(ev)
    for fecha, evs in por_dia.items():
        datos["dias"][fecha] = sorted(evs, key=lambda e: e["inicio"])
    datos["recibido_ts"] = ahora
    _recorta_ventana(datos, ahora)
    _escribe(datos)
    return {"ok": True, "dias_actualizados": sorted(por_dia), "eventos_guardados": len(validados)}


def _recorta_ventana(datos: dict, ahora: float):
    """RNF de RF-5 (sin archivo historico): se guarda como mucho hoy y
    mañana, aunque la validacion (VENTANA_DIAS=2) haya aceptado un evento
    de pasado mañana -- un dia que ya paso, o uno demasiado futuro, se poda
    solo, sin que nadie tenga que limpiarlo a mano."""
    limite_atras = time.strftime("%Y-%m-%d", time.localtime(ahora - 86400))
    limite_adelante = time.strftime("%Y-%m-%d", time.localtime(ahora + 86400))
    datos["dias"] = {f: evs for f, evs in datos["dias"].items()
                      if limite_atras <= f <= limite_adelante}


def _lee() -> dict:
    if not RUTA.exists():
        return {"recibido_ts": None, "dias": {}}
    try:
        datos = json.loads(RUTA.read_text())
        datos.setdefault("dias", {})
        return datos
    except (json.JSONDecodeError, OSError) as e:
        log.warning("agenda.json ilegible, se trata como vacia: %s", e)
        return {"recibido_ts": None, "dias": {}}


def _escribe(datos: dict):
    RUTA.write_text(json.dumps(datos, ensure_ascii=False, indent=2))


def eventos_de(fecha: str) -> list:
    """fecha en 'YYYY-MM-DD'. Lista vacia si no hay nada guardado para ese
    dia -- nunca falla, es la garantia de RF-6 (el calendario no puede
    tumbar el dia)."""
    return list(_lee().get("dias", {}).get(fecha, []))


def horas_desde_ultimo_post(ts: float = None) -> float | None:
    """D-3: frescura, no un booleano. None si nunca se recibio nada."""
    datos = _lee()
    recibido = datos.get("recibido_ts")
    if recibido is None:
        return None
    ahora = ts if ts is not None else time.time()
    return round((ahora - recibido) / 3600, 2)


def snapshot() -> dict:
    """Para GET /api/agenda: lo guardado + hace cuanto, sin exponer nada
    fuera de CAMPOS_EVENTO (ya filtrado al guardar, pero se repite el
    contrato aqui como documentacion ejecutable)."""
    datos = _lee()
    return {
        "recibido_ts": datos.get("recibido_ts"),
        "horas_desde_ultimo_post": horas_desde_ultimo_post(),
        "dias": datos.get("dias", {}),
    }
