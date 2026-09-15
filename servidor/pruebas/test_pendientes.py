"""Pendientes del dia (cerebro-jornada, MISION.md §2a). Sin red: solo el
fichero JSON local, redirigido a una ruta temporal para no tocar el de
verdad ni depender de su estado previo.
"""
import os, sys, tempfile
_SRV = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_SRV)
import importlib.util
from pathlib import Path


def carga(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


P = carga("pendientes", "nucleo/pendientes.py")

ok = fallo = 0


def afirma(nombre, cond, detalle=""):
    global ok, fallo
    if cond:
        ok += 1
        print(f"  ok  {nombre}")
    else:
        fallo += 1
        print(f"  FALLO {nombre}" + (f": {detalle}" if detalle else ""))


with tempfile.TemporaryDirectory() as tmp:
    P.RUTA = Path(tmp) / "pendientes.json"

    print("-- agregar y listar --")
    a = P.agrega("revisar PR de ola 2", "09:30")
    b = P.agrega("preparar demo NTT DATA")
    afirma("ids consecutivos", b["id"] == a["id"] + 1, (a["id"], b["id"]))
    l = P.lista()
    afirma("los dos aparecen", len(l) == 2, l)
    afirma("el que tiene hora va primero", l[0]["id"] == a["id"], l)

    print("\n-- completar --")
    r = P.completa(a["id"])
    afirma("completa devuelve el item", r is not None and r["hecho"])
    afirma("ya no aparece en la lista activa", len(P.lista()) == 1, P.lista())
    afirma("pero si con incluir_hechos", len(P.lista(incluir_hechos=True)) == 2)
    afirma("completar un id inexistente no revienta", P.completa(9999) is None)

    print("\n-- persistencia: se relee del disco, no de memoria --")
    P2 = carga("pendientes2", "nucleo/pendientes.py")
    P2.RUTA = P.RUTA
    afirma("otro modulo ve los mismos datos", len(P2.lista(incluir_hechos=True)) == 2)

    print("\n-- tope: no crece sin limite (RNF-3, mismo espiritu que superpower) --")
    P.RUTA = Path(tmp) / "pendientes_grandes.json"
    P.TOPE = 5
    for i in range(20):
        item = P.agrega(f"tarea {i}")
        P.completa(item["id"])
    afirma("los completados respetan el tope",
           len(P.lista(incluir_hechos=True)) <= 5, len(P.lista(incluir_hechos=True)))

    print("\n-- fichero corrupto no revienta, se trata como vacio --")
    ruta_mala = Path(tmp) / "corrupto.json"
    ruta_mala.write_text("{esto no es json valido")
    P.RUTA = ruta_mala
    afirma("lista() no revienta", P.lista() == [])

print("\n" + "=" * 46)
print(f"  {ok} correctos, {fallo} fallos")
print("=" * 46)
sys.exit(1 if fallo else 0)
