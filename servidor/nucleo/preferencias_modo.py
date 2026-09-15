"""
Preferencias de modo — RF-17 (.kiro/specs/ritmo/requirements.md), aprendizaje
sencillo sin telemetria: cuando el humano corrige a mano el modo que
`planificador.py::_infiere_modo()` le puso a un pendiente, esa correccion se
recuerda y pesa MAS que la heuristica por palabra clave la proxima vez que
aparezca un titulo igual.

DISEÑO: mismo patron que nucleo/pendientes.py -- un JSON local, se lee y se
reescribe entero. Deliberadamente NO es un modelo que "aprende": es un
diccionario titulo_normalizado -> modo, la correccion mas reciente gana. Es
la version mas simple que cumple RF-17 sin mandar nada a un proveedor
externo ni reentrenar nada -- ver docs/investigacion/estado-del-arte-tdah-
2026-actualizacion.md §5.1 para el porque de quedarse deliberadamente en
esta simplicidad.
"""
import json, logging, re, time
from pathlib import Path

log = logging.getLogger("preferencias_modo")

AQUI = Path(__file__).resolve().parent.parent   # server/brain/
RUTA = AQUI / "preferencias_modo.json"

# Tope generoso: cada entrada es un titulo corto + un modo. Miles de
# correcciones distintas son ya mas pendientes de los que alguien real
# genera en años -- pero se pone un limite para que el fichero no crezca
# sin fin si algun dia se automatiza la escritura.
TOPE = 2000


def normaliza(texto: str) -> str:
    """Mismo texto, minusculas, sin espacios de mas -- para que 'Preparar
    Demo' y 'preparar demo' (o con doble espacio) sean la misma clave.
    Deliberadamente NO se hace fuzzy-matching (distancia de edicion,
    embeddings): con exact-match-normalizado ya se resuelve el caso real
    (el mismo pendiente recurrente, re-tecleado igual o casi igual) sin el
    riesgo de una coincidencia falsa uniendo dos tareas distintas."""
    return re.sub(r"\s+", " ", str(texto).strip().lower())


def _lee() -> dict:
    if not RUTA.exists():
        return {}
    try:
        return json.loads(RUTA.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("preferencias_modo.json ilegible, se trata como vacio: %s", e)
        return {}


def _escribe(datos: dict):
    if len(datos) > TOPE:
        # Poda lo mas viejo por orden de insercion (dict de Python preserva
        # orden) -- no hay timestamp por entrada, no vale la pena el peso
        # extra en el fichero solo para una poda que casi nunca se activa.
        datos = dict(list(datos.items())[-TOPE:])
    RUTA.write_text(json.dumps(datos, ensure_ascii=False, indent=2))


def guarda(titulo: str, modo: str) -> None:
    """Registra que 'titulo' (normalizado) deberia clasificarse como
    'modo'. Se llama cuando el humano corrige a mano un bloque de origen
    'pendiente' a un modo distinto del que la heuristica le puso."""
    clave = normaliza(titulo)
    if not clave:
        return
    datos = _lee()
    datos[clave] = modo
    _escribe(datos)


def consulta(titulo: str) -> str | None:
    """None si nunca se corrigio ese titulo -- deja que la heuristica por
    palabra clave decida, como hasta ahora."""
    return _lee().get(normaliza(titulo))


def todas() -> dict:
    return dict(_lee())
