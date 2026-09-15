"""Pruebas de nucleo/planificador.py::arma_bloques -- pura, sin disco."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleo.planificador import arma_bloques, _infiere_modo, _epoch_de_hora

ok = 0
fallos = []


def check(nombre, cond):
    global ok
    if cond:
        ok += 1
        print(f"  ok  {nombre}")
    else:
        fallos.append(nombre)
        print(f"  FALLO  {nombre}")


print("\n-- heuristica de modo por palabra clave --")
check("codigo -> construccion", _infiere_modo("arreglar bug del websocket") == "construccion")
check("certificacion -> estudio", _infiere_modo("estudiar para certificacion NVIDIA") == "estudio")
check("propuesta -> oferta", _infiere_modo("armar propuesta para cliente X") == "oferta")
check("investigar -> investigacion", _infiere_modo("investigar Calm Technology") == "investigacion")
check("reunion -> reunion", _infiere_modo("reunion con el equipo de spatial") == "reunion")
check("sin pista -> multitarea", _infiere_modo("responder correos sueltos") == "multitarea")
check("construccion gana sobre investigacion en solape",
      _infiere_modo("investigar y arreglar el bug de firmware") == "construccion")


print("\n-- hora valida se convierte a epoch del mismo dia que ahora_epoch --")
ahora = time.mktime((2026, 9, 15, 10, 0, 0, 0, 0, -1))
e = _epoch_de_hora("14:30", ahora)
lt = time.localtime(e)
check("misma fecha", (lt.tm_year, lt.tm_mon, lt.tm_mday) == (2026, 9, 15))
check("hora correcta", (lt.tm_hour, lt.tm_min) == (14, 30))
check("hora invalida devuelve None", _epoch_de_hora("no es una hora", ahora) is None)
check("hora vacia devuelve None", _epoch_de_hora("", ahora) is None)


print("\n-- arma_bloques: anclados por hora primero, en orden cronologico --")
pendientes = [
    {"id": 1, "texto": "reunion cliente NTT", "hora": "15:00", "hecho": False},
    {"id": 2, "texto": "estudiar cert nvidia", "hora": "", "hecho": False},
    {"id": 3, "texto": "llamada equipo", "hora": "09:00", "hecho": False},
    {"id": 4, "texto": "ya hecho esto", "hora": "", "hecho": True},   # se descarta
]
bloques = arma_bloques(pendientes, ahora_epoch=ahora)
check("descarta los hechos", len(bloques) == 3)
check("primero el de las 9:00 (anclado, cronologico)", bloques[0]["titulo"] == "llamada equipo")
check("segundo el de las 15:00", bloques[1]["titulo"] == "reunion cliente NTT")
check("tercero el sin hora", bloques[2]["titulo"] == "estudiar cert nvidia")
check("los anclados llevan anclado=True", bloques[0]["anclado"] and bloques[1]["anclado"])
check("el sin hora lleva anclado=False", bloques[2]["anclado"] is False)
check("modo inferido correcto en el anclado", bloques[1]["modo"] == "reunion")
check("via = modo por defecto", bloques[2]["via"] == "estudio")
check("ids con prefijo pend- (no chocan con ids de otro origen)",
      all(b["id"].startswith("pend-") for b in bloques))


print("\n-- arma_bloques: texto vacio se descarta, no revienta --")
raros = [{"id": 5, "texto": "  ", "hora": "", "hecho": False},
         {"id": 6, "texto": "algo real", "hora": "", "hecho": False}]
b2 = arma_bloques(raros, ahora_epoch=ahora)
check("texto vacio/blank descartado", len(b2) == 1 and b2[0]["titulo"] == "algo real")


print("\n-- arma_bloques: anclados_extra (agenda) se intercalan por hora --")
extra = [{"id": "agenda-1", "titulo": "sync con cliente", "modo": "reunion",
          "anclado": True, "inicio_previsto": _epoch_de_hora("11:00", ahora),
          "origen": "agenda", "via": "agenda"}]
b3 = arma_bloques(pendientes[:2], ahora_epoch=ahora, anclados_extra=extra)
titulos_en_orden = [b["titulo"] for b in b3 if b["inicio_previsto"] is not None]
check("agenda intercalada cronologicamente entre los anclados de pendientes",
      titulos_en_orden == ["sync con cliente", "reunion cliente NTT"])


print("\n-- arma_bloques con lista vacia no revienta --")
check("lista vacia -> plan vacio", arma_bloques([], ahora_epoch=ahora) == [])


print("\n-- agenda_anclados_extra: filtro de calendario (RF-7, Fase 2) --")
import nucleo.agenda as _agenda_mod
from nucleo.planificador import agenda_anclados_extra
_d = tempfile.mkdtemp()
_agenda_mod.RUTA = __import__("pathlib").Path(_d) / "agenda.json"
_agenda_mod.guarda([
    {"inicio": "2026-09-15T09:00:00", "fin": "2026-09-15T09:30:00",
     "titulo": "Standup", "calendario": "trabajo", "organizador": False, "modo": "reunion"},
    {"inicio": "2026-09-15T19:00:00", "fin": "2026-09-15T20:00:00",
     "titulo": "Yoga", "calendario": "personal", "organizador": True, "modo": "descanso"},
], ts=ahora)
sin_filtro = agenda_anclados_extra(ahora_epoch=ahora)
check("sin filtro trae los 2", len(sin_filtro) == 2)
solo_trabajo = agenda_anclados_extra(ahora_epoch=ahora, ocultar_calendarios={"personal"})
check("ocultando personal, solo queda trabajo", [b["titulo"] for b in solo_trabajo] == ["Standup"])
ninguno = agenda_anclados_extra(ahora_epoch=ahora, ocultar_calendarios={"trabajo", "personal"})
check("ocultando ambos, plan vacio", ninguno == [])


print("\n-- _infiere_modo: overrides_modo (RF-17) pesa mas que la heuristica --")
check("sin overrides, heuristica normal", _infiere_modo("responder correos sueltos") == "multitarea")
check("con override para ese titulo exacto, gana el override",
      _infiere_modo("responder correos sueltos", overrides={"responder correos sueltos": "construccion"})
      == "construccion")
check("override normaliza igual que preferencias_modo.normaliza",
      _infiere_modo("Responder   Correos Sueltos",
                    overrides={"responder correos sueltos": "construccion"}) == "construccion")
check("titulo no presente en overrides cae a la heuristica",
      _infiere_modo("arreglar bug del websocket", overrides={"otra cosa": "estudio"}) == "construccion")
check("overrides vacio/None no cambia nada",
      _infiere_modo("arreglar bug del websocket", overrides={}) == "construccion")


print("\n-- arma_bloques: overrides_modo se pasa a cada pendiente (RF-17) --")
pend_override = [{"id": 1, "texto": "responder correos sueltos", "hecho": False, "hora": ""}]
b_sin = arma_bloques(pend_override, ahora_epoch=ahora)
check("sin overrides_modo, heuristica normal", b_sin[0]["modo"] == "multitarea")
b_con = arma_bloques(pend_override, ahora_epoch=ahora,
                      overrides_modo={"responder correos sueltos": "construccion"})
check("con overrides_modo, el bloque hereda la correccion", b_con[0]["modo"] == "construccion")


print("\n-- arma_bloques: horas_pico reordena lo profundo (RF-18) --")
pend_mixto = [
    {"id": 1, "texto": "responder correos sueltos", "hecho": False, "hora": ""},   # multitarea
    {"id": 2, "texto": "arreglar bug del websocket", "hecho": False, "hora": ""},  # construccion (profundo)
]
hora_actual = time.localtime(ahora).tm_hour
b_pico = arma_bloques(pend_mixto, ahora_epoch=ahora, horas_pico=[hora_actual])
check("en hora pico, lo profundo va primero entre lo suelto",
      [b["titulo"] for b in b_pico] == ["arreglar bug del websocket", "responder correos sueltos"])
otra_hora = (hora_actual + 5) % 24
b_fuera = arma_bloques(pend_mixto, ahora_epoch=ahora, horas_pico=[otra_hora])
check("fuera de pico, lo profundo se empuja al final",
      [b["titulo"] for b in b_fuera] == ["responder correos sueltos", "arreglar bug del websocket"])
b_sin_pico = arma_bloques(pend_mixto, ahora_epoch=ahora)
check("sin horas_pico, se conserva el orden de llegada (cero regresion)",
      [b["titulo"] for b in b_sin_pico] == ["responder correos sueltos", "arreglar bug del websocket"])
b_pico_vacio = arma_bloques(pend_mixto, ahora_epoch=ahora, horas_pico=[])
check("horas_pico vacia se comporta como None (cero regresion)",
      [b["titulo"] for b in b_pico_vacio] == ["responder correos sueltos", "arreglar bug del websocket"])


print("\n" + "=" * 46)
if fallos:
    print(f"  {ok} correctos, {len(fallos)} FALLOS: {fallos}")
    print("=" * 46)
    sys.exit(1)
else:
    print(f"  {ok} correctos, 0 fallos")
    print("=" * 46)
