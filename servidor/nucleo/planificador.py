"""
Planificador — arma el plan del dia (ola ritmo, Fase 2 de
.kiro/specs/ritmo/tasks.md: "plan_del_dia(): arma bloques desde
pendientes.py + bloques anclados de la ola agenda (si existe) + los dichos
por voz").

DISEÑO: separado de nucleo/ritmo.py a proposito. MotorRitmo es puro (sin
I/O, ver la cabecera de ese modulo); leer pendientes.json SI es I/O
(nucleo/pendientes.py toca disco). Mezclar las dos cosas en un mismo modulo
habria obligado a MotorRitmo a dejar de ser puro, o a esconder el I/O detras
de un mock feo en las pruebas. Aqui se separa en dos capas:

  arma_bloques(pendientes, ahora_epoch)  -- PURA, testeable sin disco
  plan_del_dia()                         -- hace el I/O (lee pendientes.json)
                                             y llama a la pura de arriba

Heuristica v1 de modo (RF-2 tabla, docs/investigacion/estado-del-arte-tdah.md
§3): por palabras clave en el texto del pendiente. Es deliberadamente
simple y determinista -- nada de LLM aqui, porque un plan del dia tiene que
poder armarse sin red y dar SIEMPRE el mismo resultado para el mismo
`pendientes.json` (facil de probar, facil de razonar). Un agente (via
mcps/ritmo.py:ritmo_plan) puede sobreescribir el plan con algo mas fino
cuando haga falta -- esta heuristica es el piso, no el techo.
"""
import re
import time

# Orden de las reglas SI importa: la primera que matchea gana. Puesto asi
# porque algunas palabras se solapan ("revisar codigo" podria leerse como
# 'investigacion' si 'codigo' no fuera mas especifico primero).
_REGLAS_MODO = [
    ("construccion",  r"\b(codigo|bug|firmware|implementar|arreglar|deploy|desplegar)\b"),
    ("estudio",       r"\b(estudiar|certificacion|curso|examen|practicar)\b"),
    ("oferta",        r"\b(oferta|propuesta|cotizacion|presupuesto)\b"),
    ("investigacion", r"\b(investigar|research|leer sobre|explorar)\b"),
    ("arquitectura",  r"\b(diseñar|disenar|arquitectura|planificar sistema)\b"),
    ("material",      r"\b(charla|demo|presentacion|webinar|evento)\b"),
    ("reunion",       r"\b(reunion|llamada|call|sync|1:1|uno a uno)\b"),
]

_HORA_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

# RF-18: modos "profundos" -- los que vale la pena empujar hacia una
# ventana de HyperFocus real (histograma de nucleo/historial_foco.py) en
# vez de dejarlos al primero que llegue. 'reunion' y 'descanso' quedan
# fuera a proposito: la reunion casi siempre viene anclada (no pasa por
# aqui) y el descanso no se beneficia de "mas foco".
MODOS_PROFUNDOS = {"material", "oferta", "investigacion", "construccion",
                    "arquitectura", "estudio"}


def _infiere_modo(texto: str, overrides: dict = None) -> str:
    """RF-17: si el humano ya corrigio este titulo antes
    (nucleo/preferencias_modo.py), esa correccion pesa MAS que la
    heuristica por palabra clave -- 'overrides' se pasa ya normalizado
    (mismas claves que preferencias_modo.normaliza produce)."""
    if overrides:
        from nucleo.preferencias_modo import normaliza as _normaliza
        corregido = overrides.get(_normaliza(texto))
        if corregido:
            return corregido
    t = texto.lower()
    for modo, patron in _REGLAS_MODO:
        if re.search(patron, t):
            return modo
    return "multitarea"   # sin pista clara: el modo mas corto y menos exigente


def _epoch_de_hora(hora: str, ahora_epoch: float):
    """'09:30' -> epoch de hoy a las 9:30, en la MISMA fecha que ahora_epoch
    (para que las pruebas con reloj falso no dependan de la fecha real de
    la maquina). None si 'hora' no tiene ese formato -- sigue siendo un
    pendiente valido, solo que no ancla el plan (texto libre tipo 'antes
    del almuerzo', que pendientes.py explicitamente permite)."""
    m = _HORA_RE.match(hora.strip())
    if not m:
        return None
    base = time.localtime(ahora_epoch)
    hh, mm = int(m.group(1)), int(m.group(2))
    return time.mktime((base.tm_year, base.tm_mon, base.tm_mday, hh, mm, 0,
                         0, 0, -1))


def arma_bloques(pendientes: list[dict], ahora_epoch: float = None,
                  anclados_extra: list[dict] = None,
                  overrides_modo: dict = None,
                  horas_pico: list = None) -> list[dict]:
    """Traduce una lista de pendientes (forma de nucleo/pendientes.py::lista())
    a bloques listos para RITMO.plan_set(). Pura: mismo input, mismo output,
    sin tocar disco ni reloj real salvo por 'ahora_epoch' explicito.

    'anclados_extra': bloques ya armados (p.ej. de la ola agenda, cuando
    exista) que se intercalan por 'inicio_previsto'. Se aceptan tal cual,
    sin reinterpretar su modo -- ese es el contrato con quien los arma.

    'overrides_modo' (RF-17): dict normalizado titulo->modo, de
    nucleo/preferencias_modo.py::todas(). 'horas_pico' (RF-18): lista de
    horas (0-23) de nucleo/historial_foco.py::horas_pico(). Ambos opcionales
    y con default None -- sin ellos, el comportamiento es EXACTAMENTE el de
    antes de RF-17/18 (cero regresion sobre lo ya probado en Fase 1/2).
    """
    ahora_epoch = time.time() if ahora_epoch is None else ahora_epoch
    bloques = []
    for p in pendientes:
        if p.get("hecho"):
            continue
        texto = str(p.get("texto", "")).strip()
        if not texto:
            continue
        hora = str(p.get("hora", "")).strip()
        modo = _infiere_modo(texto, overrides=overrides_modo)
        inicio = _epoch_de_hora(hora, ahora_epoch) if hora else None
        bloques.append({
            "id": f"pend-{p.get('id')}",
            "titulo": texto,
            "modo": modo,
            "anclado": inicio is not None,
            "inicio_previsto": inicio,
            "origen": "pendiente",
            "via": modo,
        })
    bloques.extend(anclados_extra or [])
    # Orden: primero lo anclado por hora (cronologico), despues lo suelto en
    # el orden en que llego -- mismo criterio que pendientes.py::lista().
    con_hora = sorted((b for b in bloques if b["inicio_previsto"] is not None),
                       key=lambda b: b["inicio_previsto"])
    sin_hora = [b for b in bloques if b["inicio_previsto"] is None]
    if horas_pico:
        hora_actual = time.localtime(ahora_epoch).tm_hour
        en_pico = hora_actual in horas_pico
        # En hora pico: lo profundo primero, para aprovechar la ventana YA.
        # Fuera de pico: lo profundo se empuja al final, con la esperanza
        # (RF-18, sin garantia -- esto es orden, no un slot fijo) de que
        # una ventana de foco real llegue antes de que le toque su turno.
        # sorted() es estable: dentro de cada grupo se conserva el orden
        # de llegada de siempre.
        sin_hora = sorted(
            sin_hora,
            key=lambda b: (0 if (b["modo"] in MODOS_PROFUNDOS) == en_pico else 1))
    return con_hora + sin_hora


def agenda_anclados_extra(ahora_epoch: float = None,
                            ocultar_calendarios: set = None) -> list[dict]:
    """Ola agenda (D-4 de .kiro/specs/agenda/design.md): traduce lo que haya
    en agenda.json para HOY a la forma de bloque que arma_bloques() espera.

    'ocultar_calendarios' (Fase 2, RF-7 "separación visible trabajo /
    personal"): un set con 'trabajo' y/o 'personal' -- los eventos de esos
    calendarios se excluyen del plan. Filtrado AQUI, no en nucleo/agenda.py:
    el dato se guarda completo (RF-2 ya decide que campos, no cuales
    calendarios); ocultar es una preferencia de visualizacion/planificacion,
    no de almacenamiento -- cambiar el toggle no debe requerir re-mandar el
    POST del Atajo.

    Import local a proposito -- evita que nucleo/planificador.py dependa de
    nucleo/agenda.py a nivel de modulo (y con ello de un ciclo si algun dia
    agenda necesitara importar algo de aqui). Sin agenda.json, o sin nada
    guardado para hoy, devuelve lista vacia: RF-6 de agenda ("el calendario
    no puede tumbar el dia") empieza aqui mismo.
    """
    from nucleo import agenda as _agenda
    ahora_epoch = time.time() if ahora_epoch is None else ahora_epoch
    hoy = time.strftime("%Y-%m-%d", time.localtime(ahora_epoch))
    ocultar = ocultar_calendarios or set()
    extra = []
    for i, ev in enumerate(_agenda.eventos_de(hoy)):
        if ev.get("calendario") in ocultar:
            continue
        try:
            inicio = time.mktime(time.strptime(ev["inicio"][:19], "%Y-%m-%dT%H:%M:%S"))
            fin = time.mktime(time.strptime(ev["fin"][:19], "%Y-%m-%dT%H:%M:%S"))
        except (KeyError, ValueError):
            continue   # un evento mal formado no debe tumbar TODO el plan
        duracion_min = max(1, round((fin - inicio) / 60))
        extra.append({
            "id": f"agenda-{hoy}-{i}", "titulo": ev["titulo"], "modo": ev["modo"],
            "duracion_min": duracion_min, "anclado": True, "inicio_previsto": inicio,
            "origen": "agenda", "via": ev.get("calendario", "agenda"),
        })
    return extra


def plan_del_dia(anclados_extra: list[dict] = None,
                  ocultar_calendarios: set = None) -> list[dict]:
    """Punto de entrada real: lee pendientes.json (+ agenda.json si hay algo
    para hoy, + preferencias_modo.json para RF-17, + historial_foco.json
    para RF-18) y arma el plan de hoy.

    Separado de arma_bloques() para que esa quede 100% pura y testeable sin
    disco -- ver el porque en la cabecera del modulo. 'anclados_extra'
    explicito sigue aceptandose (p.ej. para pruebas) y, si se pasa, se
    ANADE a lo que traiga agenda.json, no lo reemplaza. 'ocultar_calendarios'
    ver agenda_anclados_extra().
    """
    from nucleo.pendientes import lista as _lista_pendientes
    from nucleo.preferencias_modo import todas as _preferencias_todas
    from nucleo.historial_foco import horas_pico as _horas_pico
    extra = agenda_anclados_extra(ocultar_calendarios=ocultar_calendarios)
    if anclados_extra:
        extra = extra + list(anclados_extra)
    return arma_bloques(_lista_pendientes(), anclados_extra=extra,
                         overrides_modo=_preferencias_todas(),
                         horas_pico=_horas_pico())
