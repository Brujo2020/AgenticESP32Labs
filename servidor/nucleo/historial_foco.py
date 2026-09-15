"""
Historial de foco — RF-18 (.kiro/specs/ritmo/requirements.md): las horas del
dia donde `nucleo/foco.py::MotorFoco` entra de verdad en FOCO, acumuladas en
el tiempo, para que `planificador.py` pueda ordenar los bloques extensibles
hacia las ventanas donde el HyperFocus realmente ocurre -- la misma señal
que un wearable de pago (Oura, WHOOP) infiere indirectamente, aqui gratis,
de datos que el sistema ya genera solo.

DISEÑO DELIBERADO -- este modulo hace I/O, nucleo/foco.py sigue sin hacerlo.
MotorFoco es puro a proposito (ver la cabecera de ese modulo); registrar
CUANDO entra en FOCO es responsabilidad de quien lo observa desde fuera
(websocket_bridge.py, comparando MOTOR.estado entre ciclos de
vigila_superpower()), nunca de MotorFoco mismo -- mismo principio de
separacion que ya se aplico para agenda (nucleo/planificador.py vs
nucleo/agenda.py): un modulo puro y testeable en memoria, otro que persiste,
nunca los dos mezclados.
"""
import json, logging, time
from pathlib import Path

log = logging.getLogger("historial_foco")

AQUI = Path(__file__).resolve().parent.parent   # server/brain/
RUTA = AQUI / "historial_foco.json"

# ~2000 entradas de "entre en FOCO" son meses de uso real (unas pocas por
# dia) -- de sobra para el histograma y lejos de crecer sin limite.
TOPE = 2000

# Con menos muestras que esto en la ventana de dias, horas_pico() devuelve
# vacio: mejor no imponer un patron todavia debil que "ordenar" el dia mal
# con dos datos sueltos.
MINIMO_MUESTRAS = 8


def _lee() -> list:
    if not RUTA.exists():
        return []
    try:
        return json.loads(RUTA.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("historial_foco.json ilegible, se trata como vacio: %s", e)
        return []


def _escribe(items: list):
    RUTA.write_text(json.dumps(items[-TOPE:], ensure_ascii=False))


def registra_entrada_foco(ts: float = None) -> None:
    """Llamar UNA vez por cada transicion real a FOCO (no en cada tick
    mientras se sigue en FOCO) -- websocket_bridge.py es quien detecta la
    transicion comparando MOTOR.estado entre ciclos."""
    ts = time.time() if ts is None else ts
    items = _lee()
    items.append({"ts": ts, "hora": time.localtime(ts).tm_hour})
    _escribe(items)


def histograma(dias: int = 21, ts: float = None) -> dict:
    """Cuenta de entradas a FOCO por hora del dia (0-23), en los ultimos
    'dias'. Una ventana movil, no todo el historico: los habitos cambian
    (una jornada distinta, un proyecto distinto) y pesar igual una entrada
    de hace 6 meses que una de ayer daria una foto vieja."""
    ts = time.time() if ts is None else ts
    corte = ts - dias * 86400
    hist = {h: 0 for h in range(24)}
    for item in _lee():
        if item.get("ts", 0) >= corte:
            hist[item.get("hora", -1) % 24] += 1
    return hist


def horas_pico(top_n: int = 4, dias: int = 21, ts: float = None) -> list:
    """Las 'top_n' horas con mas entradas a FOCO, ordenadas de menor a
    mayor (para poder comparar directo contra la hora actual). Lista vacia
    si no hay muestras suficientes (MINIMO_MUESTRAS) -- señal explicita de
    "todavia no se sabe" para quien llama, en vez de un patron inventado."""
    hist = histograma(dias=dias, ts=ts)
    total = sum(hist.values())
    if total < MINIMO_MUESTRAS:
        return []
    picos = sorted(hist.items(), key=lambda kv: -kv[1])[:top_n]
    return sorted(h for h, c in picos if c > 0)
