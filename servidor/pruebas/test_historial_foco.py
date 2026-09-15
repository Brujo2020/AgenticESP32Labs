"""Pruebas de nucleo/historial_foco.py -- RF-18. Fichero real en un
directorio temporal (patch de RUTA), reloj falso via 'ts' explicito, sin
red ni servidor HTTP."""
import os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleo.historial_foco as hf

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
    d = tempfile.mkdtemp()
    hf.RUTA = Path(d) / "historial_foco.json"


AHORA = time.mktime((2026, 9, 15, 10, 0, 0, 0, 0, -1))


def _ts_hora(hora, dias_atras=0):
    return AHORA - dias_atras * 86400 - (time.localtime(AHORA).tm_hour - hora) * 3600


# ============================================================
print("\n-- histograma() vacio sin fichero --")
_tmp()
h = hf.histograma(ts=AHORA)
check("24 horas, todas en cero", len(h) == 24 and all(v == 0 for v in h.values()))

# ============================================================
print("\n-- registra_entrada_foco() + histograma() --")
_tmp()
hf.registra_entrada_foco(ts=_ts_hora(9))
hf.registra_entrada_foco(ts=_ts_hora(9))
hf.registra_entrada_foco(ts=_ts_hora(15))
h = hf.histograma(ts=AHORA)
check("cuenta las entradas por hora", h[9] == 2 and h[15] == 1)

# ============================================================
print("\n-- histograma() respeta la ventana de dias --")
_tmp()
hf.registra_entrada_foco(ts=_ts_hora(9, dias_atras=0))
hf.registra_entrada_foco(ts=_ts_hora(9, dias_atras=30))   # fuera de la ventana de 21 dias
h = hf.histograma(dias=21, ts=AHORA)
check("solo cuenta lo dentro de la ventana", h[9] == 1)

# ============================================================
print("\n-- horas_pico() vacio si hay pocas muestras --")
_tmp()
for _ in range(hf.MINIMO_MUESTRAS - 1):
    hf.registra_entrada_foco(ts=_ts_hora(9))
check("menos del minimo -> lista vacia", hf.horas_pico(ts=AHORA) == [])

# ============================================================
print("\n-- horas_pico() con muestras suficientes --")
_tmp()
for _ in range(10):
    hf.registra_entrada_foco(ts=_ts_hora(9))
for _ in range(6):
    hf.registra_entrada_foco(ts=_ts_hora(16))
picos = hf.horas_pico(top_n=2, ts=AHORA)
check("devuelve las horas con mas entradas, ordenadas", picos == [9, 16])

# ============================================================
print("\n-- horas_pico() top_n recorta y no incluye horas en cero --")
_tmp()
for _ in range(hf.MINIMO_MUESTRAS):
    hf.registra_entrada_foco(ts=_ts_hora(9))
picos = hf.horas_pico(top_n=4, ts=AHORA)
check("solo la hora con entradas reales, aunque top_n sea mayor", picos == [9])

# ============================================================
print("\n-- persiste entre llamadas (releido de disco) --")
_tmp()
hf.registra_entrada_foco(ts=_ts_hora(11))
items = hf._lee()
check("una entrada guardada en disco", len(items) == 1 and items[0]["hora"] == 11)

# ============================================================
print("\n-- fichero corrupto se trata como vacio, no revienta --")
_tmp()
hf.RUTA.write_text("no es json {{{")
check("histograma no revienta con json corrupto",
      all(v == 0 for v in hf.histograma(ts=AHORA).values()))

# ============================================================
print("\n-- poda por TOPE --")
_tmp()
viejo_tope = hf.TOPE
hf.TOPE = 5
try:
    for _ in range(8):
        hf.registra_entrada_foco(ts=_ts_hora(9))
    items = hf._lee()
    check("se poda al superar el tope", len(items) == hf.TOPE)
finally:
    hf.TOPE = viejo_tope

# ============================================================
print(f"\n{ok} pruebas OK, {len(fallos)} fallos")
if fallos:
    print("Fallaron:", fallos)
    sys.exit(1)
