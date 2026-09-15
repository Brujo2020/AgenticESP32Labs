"""Pruebas de nucleo/agenda.py -- los 5 criterios de aceptacion de
.kiro/specs/agenda/requirements.md. Fichero real en un directorio temporal
(patch de RUTA), sin red ni servidor HTTP."""
import os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleo.agenda as agenda

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


def _tmp():
    """Redirige RUTA a un fichero temporal por prueba -- aisla cada bloque
    igual que test_pendientes.py hace con su propio json."""
    d = tempfile.mkdtemp()
    agenda.RUTA = Path(d) / "agenda.json"


def _iso(ahora, dias=0, hora="15:00:00"):
    t = time.localtime(ahora + dias * 86400)
    fecha = time.strftime("%Y-%m-%d", t)
    return f"{fecha}T{hora}"


AHORA = time.mktime((2026, 9, 15, 10, 0, 0, 0, 0, -1))


# ============================================================
print("\n-- criterio 1: 5 eventos guardados, aparecen en eventos_de() --")
_tmp()
eventos = [
    {"inicio": _iso(AHORA, hora="09:00:00"), "fin": _iso(AHORA, hora="09:30:00"),
     "titulo": "Standup", "calendario": "trabajo", "organizador": False},
    {"inicio": _iso(AHORA, hora="15:00:00"), "fin": _iso(AHORA, hora="15:30:00"),
     "titulo": "Reunion cliente NTT", "calendario": "trabajo", "organizador": False, "modo": "reunion"},
    {"inicio": _iso(AHORA, hora="11:00:00"), "fin": _iso(AHORA, hora="12:00:00"),
     "titulo": "Preparar demo", "calendario": "trabajo", "organizador": True, "modo": "material"},
    {"inicio": _iso(AHORA, hora="18:00:00"), "fin": _iso(AHORA, hora="19:00:00"),
     "titulo": "Yoga", "calendario": "personal", "organizador": True},
    {"inicio": _iso(AHORA, hora="20:00:00"), "fin": _iso(AHORA, hora="20:30:00"),
     "titulo": "Llamar a mama", "calendario": "personal", "organizador": True},
]
r = agenda.guarda(eventos, ts=AHORA)
check("5 guardados", r["eventos_guardados"] == 5)
hoy = time.strftime("%Y-%m-%d", time.localtime(AHORA))
dia = agenda.eventos_de(hoy)
check("5 en eventos_de(hoy)", len(dia) == 5)
check("ordenados por hora", [e["titulo"] for e in dia][0] == "Standup")
check("proximo anclado a las 15:00 es 'Reunion cliente NTT'",
      [e for e in dia if e["inicio"].endswith("15:00:00")][0]["titulo"] == "Reunion cliente NTT")


print("\n-- criterio 2: reenviar los mismos 5 no duplica --")
agenda.guarda(eventos, ts=AHORA)
check("sigue habiendo 5, no 10", len(agenda.eventos_de(hoy)) == 5)


print("\n-- criterio 3: evento invalido no modifica nada (todo o nada) --")
_tmp()
agenda.guarda(eventos[:2], ts=AHORA)   # deja 2 guardados de verdad
antes = agenda.eventos_de(hoy)
malos = eventos[:2] + [{"inicio": "no-es-fecha", "fin": "tampoco", "titulo": "x", "calendario": "trabajo"}]
try:
    agenda.guarda(malos, ts=AHORA)
    fallo_lanzado = False
except agenda.AgendaInvalida:
    fallo_lanzado = True
check("AgendaInvalida se lanza con un evento malo", fallo_lanzado)
check("lo guardado antes NO cambio (todo o nada)", agenda.eventos_de(hoy) == antes)

# calendario invalido tambien debe rechazar todo el lote
try:
    agenda.guarda([{"inicio": _iso(AHORA), "fin": _iso(AHORA), "titulo": "x", "calendario": "raro"}], ts=AHORA)
    fallo2 = False
except agenda.AgendaInvalida:
    fallo2 = True
check("calendario invalido tambien rechaza el lote", fallo2)

# evento sin token no aplica aqui (eso es panel_api.py), pero el rechazo por
# ventana de tiempo si es responsabilidad de este modulo:
lejos = [{"inicio": _iso(AHORA, dias=10), "fin": _iso(AHORA, dias=10), "titulo": "x", "calendario": "trabajo"}]
try:
    agenda.guarda(lejos, ts=AHORA)
    fallo3 = False
except agenda.AgendaInvalida:
    fallo3 = True
check("evento fuera de la ventana de ±2 dias se rechaza", fallo3)


print("\n-- criterio 4: sin POST nunca, eventos_de() no rompe --")
_tmp()
check("dia sin datos -> lista vacia, no excepcion", agenda.eventos_de("2026-01-01") == [])
check("horas_desde_ultimo_post() es None sin datos", agenda.horas_desde_ultimo_post() is None)


print("\n-- criterio 5: solo los campos de RF-2 sobreviven al guardar --")
_tmp()
sucio = [{
    "inicio": _iso(AHORA, hora="10:00:00"), "fin": _iso(AHORA, hora="10:30:00"),
    "titulo": "Con datos de mas", "calendario": "trabajo", "organizador": False,
    "invitados": ["jefe@ntt.com", "cliente@empresa.com"],
    "cuerpo": "texto confidencial de la invitacion",
    "enlace_reunion": "https://teams.microsoft.com/l/meetup/xyz",
}]
agenda.guarda(sucio, ts=AHORA)
guardado = agenda.eventos_de(hoy)[0]
check("invitados NO sobrevive", "invitados" not in guardado)
check("cuerpo NO sobrevive", "cuerpo" not in guardado)
check("enlace_reunion NO sobrevive", "enlace_reunion" not in guardado)
check("solo quedan los campos de CAMPOS_EVENTO",
      set(guardado) <= agenda.CAMPOS_EVENTO)


print("\n-- D-1: reemplazo por fecha, no fusion; otro dia no se toca --")
_tmp()
manana = [{"inicio": _iso(AHORA, dias=1, hora="09:00:00"), "fin": _iso(AHORA, dias=1, hora="09:30:00"),
           "titulo": "Reunion de mañana", "calendario": "trabajo", "organizador": False}]
agenda.guarda(eventos, ts=AHORA)
agenda.guarda(manana, ts=AHORA)
check("hoy sigue con 5 tras guardar SOLO manana", len(agenda.eventos_de(hoy)) == 5)
fecha_manana = time.strftime("%Y-%m-%d", time.localtime(AHORA + 86400))
check("manana tiene su evento", len(agenda.eventos_de(fecha_manana)) == 1)
# reemplazar hoy con un solo evento no debe tocar manana
agenda.guarda([eventos[0]], ts=AHORA)
check("hoy ahora tiene 1 (reemplazado, no fusionado)", len(agenda.eventos_de(hoy)) == 1)
check("manana sigue con 1 (no se toco)", len(agenda.eventos_de(fecha_manana)) == 1)


print("\n-- D-2: clasificacion de respaldo cuando 'modo' falta o es invalido --")
_tmp()
sin_modo_invitados = [{"inicio": _iso(AHORA, hora="09:00:00"), "fin": _iso(AHORA, hora="09:30:00"),
                        "titulo": "x", "calendario": "trabajo", "organizador": False}]
agenda.guarda(sin_modo_invitados, ts=AHORA)
check("organizador=False (con invitados) -> reunion", agenda.eventos_de(hoy)[0]["modo"] == "reunion")

_tmp()
sin_modo_trabajo = [{"inicio": _iso(AHORA, hora="09:00:00"), "fin": _iso(AHORA, hora="09:30:00"),
                      "titulo": "x", "calendario": "trabajo", "organizador": True}]
agenda.guarda(sin_modo_trabajo, ts=AHORA)
check("trabajo sin invitados -> material", agenda.eventos_de(hoy)[0]["modo"] == "material")

_tmp()
sin_modo_personal = [{"inicio": _iso(AHORA, hora="09:00:00"), "fin": _iso(AHORA, hora="09:30:00"),
                       "titulo": "x", "calendario": "personal", "organizador": True}]
agenda.guarda(sin_modo_personal, ts=AHORA)
check("personal, organizador -> multitarea", agenda.eventos_de(hoy)[0]["modo"] == "multitarea")

_tmp()
modo_basura = [{"inicio": _iso(AHORA, hora="09:00:00"), "fin": _iso(AHORA, hora="09:30:00"),
                 "titulo": "x", "calendario": "personal", "organizador": True, "modo": "no-existe"}]
agenda.guarda(modo_basura, ts=AHORA)
check("modo invalido se recalcula, no se guarda tal cual", agenda.eventos_de(hoy)[0]["modo"] == "multitarea")


print("\n-- RNF: dias fuera de hoy±1 se podan al guardar --")
_tmp()
datos_directos = {"recibido_ts": AHORA, "dias": {
    "2026-01-01": [{"inicio": "2026-01-01T09:00:00", "fin": "2026-01-01T09:30:00",
                     "titulo": "viejo", "calendario": "trabajo", "organizador": True, "modo": "multitarea"}],
}}
agenda._escribe(datos_directos)
agenda.guarda([eventos[0]], ts=AHORA)   # cualquier guardado dispara la poda
check("dia viejo podado tras el siguiente guardado", agenda.eventos_de("2026-01-01") == [])


print("\n" + "=" * 46)
if fallos:
    print(f"  {ok} correctos, {len(fallos)} FALLOS: {fallos}")
    print("=" * 46)
    sys.exit(1)
else:
    print(f"  {ok} correctos, 0 fallos")
    print("=" * 46)
