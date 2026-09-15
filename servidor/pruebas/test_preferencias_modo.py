"""Pruebas de nucleo/preferencias_modo.py -- RF-17. Fichero real en un
directorio temporal (patch de RUTA), sin red ni servidor HTTP."""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleo.preferencias_modo as pm

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
    pm.RUTA = Path(d) / "preferencias_modo.json"


# ============================================================
print("\n-- normaliza(): minusculas, espacios colapsados --")
check("mayusculas -> minusculas", pm.normaliza("Preparar Demo") == "preparar demo")
check("espacios de mas colapsan", pm.normaliza("preparar   demo  ") == "preparar demo")
check("vacio -> vacio", pm.normaliza("   ") == "")

# ============================================================
print("\n-- consulta() sobre fichero inexistente --")
_tmp()
check("None si nunca se guardo nada", pm.consulta("preparar demo") is None)

# ============================================================
print("\n-- guarda() + consulta(): ida y vuelta --")
_tmp()
pm.guarda("Preparar Demo", "material")
check("consulta devuelve lo guardado", pm.consulta("preparar demo") == "material")
check("consulta normaliza el titulo de busqueda", pm.consulta("PREPARAR   DEMO") == "material")

# ============================================================
print("\n-- guarda() con titulo vacio no hace nada --")
_tmp()
pm.guarda("   ", "material")
check("no crea fichero", not pm.RUTA.exists())

# ============================================================
print("\n-- guarda() dos veces: la mas reciente gana --")
_tmp()
pm.guarda("preparar demo", "material")
pm.guarda("preparar demo", "construccion")
check("la segunda correccion pisa la primera", pm.consulta("preparar demo") == "construccion")

# ============================================================
print("\n-- todas(): devuelve copia, no referencia al cache interno --")
_tmp()
pm.guarda("preparar demo", "material")
pm.guarda("revisar codigo", "construccion")
t = pm.todas()
check("todas() trae las dos entradas", len(t) == 2 and t.get("preparar demo") == "material"
      and t.get("revisar codigo") == "construccion")
t["preparar demo"] = "otra cosa"
check("mutar lo devuelto no afecta el disco", pm.consulta("preparar demo") == "material")

# ============================================================
print("\n-- persiste entre instancias (releido de disco) --")
_tmp()
pm.guarda("estudiar certificacion", "estudio")
ruta = pm.RUTA
pm.RUTA = ruta   # simula "otro proceso" leyendo el mismo fichero
check("se relee bien del disco", pm.consulta("estudiar certificacion") == "estudio")

# ============================================================
print("\n-- fichero corrupto se trata como vacio, no revienta --")
_tmp()
pm.RUTA.write_text("esto no es json valido {{{")
check("consulta no revienta con json corrupto", pm.consulta("x") is None)
check("todas() no revienta con json corrupto", pm.todas() == {})

# ============================================================
print("\n-- poda por TOPE --")
_tmp()
viejo_tope = pm.TOPE
pm.TOPE = 3
try:
    pm.guarda("uno", "material")
    pm.guarda("dos", "construccion")
    pm.guarda("tres", "estudio")
    pm.guarda("cuatro", "oferta")
    t = pm.todas()
    check("se poda al superar el tope", len(t) <= pm.TOPE)
    check("lo mas reciente sobrevive", t.get("cuatro") == "oferta")
finally:
    pm.TOPE = viejo_tope

# ============================================================
print(f"\n{ok} pruebas OK, {len(fallos)} fallos")
if fallos:
    print("Fallaron:", fallos)
    sys.exit(1)
