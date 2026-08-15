"""Bateria adversaria contra la guardia.

Reproduce en pequeno la metodologia del paper DCP: se lanzan llamadas
malformadas, con escalada de capacidades y con intento de inyeccion, y se
comprueba que ninguna llega al dispositivo. No pretende medir 78 % de nada;
pretende que las categorias obvias esten cubiertas y no regresen.
"""
import os, sys
# Las pruebas se resuelven desde su propia ubicacion, no desde el cwd: asi
# funcionan tanto con `python3 pruebas/x.py` desde servidor/ como desde la
# raiz del repo o desde el hook de un CI.
_SRV = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_SRV)
import importlib.util, sys, time

_s = importlib.util.spec_from_file_location("guardia", "nucleo/guardia.py")
g = importlib.util.module_from_spec(_s); _s.loader.exec_module(g)
Guardia, Rechazo = g.Guardia, g.Rechazo

ok = fallo = 0
def caso(nombre, fn, debe_rechazar=True):
    global ok, fallo
    try:
        fn(); rechazado = False
    except Rechazo:
        rechazado = True
    except Exception as e:
        print(f"  ?? {nombre}: excepcion inesperada {type(e).__name__}: {e}")
        fallo += 1; return
    if rechazado == debe_rechazar:
        ok += 1; print(f"  ok  {nombre}")
    else:
        fallo += 1
        print(f"  FALLO {nombre}: {'paso y debia rechazarse' if not rechazado else 'rechazado y debia pasar'}")

G = Guardia()
G.concede("agente", {"ver"}, 60)            # solo puede pintar vistas
G.concede("pleno", None, 60)                # todas las capacidades

print("\n-- escalada de capacidades --")
caso("hablar sin capacidad",     lambda: G.revisa("agente", "hablar", {"texto": "hola"}))
caso("preguntar sin capacidad",  lambda: G.revisa("agente", "pregunta", {"txt": "PAGO?"}))
caso("sujeto desconocido",       lambda: G.revisa("nadie", "mostrar", {"id": "x"}))
caso("comando inventado",        lambda: G.revisa("pleno", "formatear_disco", {}))
caso("mostrar con capacidad",    lambda: G.revisa("agente", "mostrar",
                                    {"id": "ci", "titulo": "CI", "filas": ["OK"]}), False)

print("\n-- concesion caducada --")
G.concede("efimero", {"ver"}, 0)
time.sleep(0.01)
caso("concesion vencida", lambda: G.revisa("efimero", "mostrar", {"id": "x"}))

print("\n-- validacion de forma --")
caso("id con mayusculas",   lambda: G.revisa("pleno", "mostrar", {"id": "MiVista"}))
caso("id con ruta",         lambda: G.revisa("pleno", "mostrar", {"id": "../../etc"}))
caso("id vacio",            lambda: G.revisa("pleno", "mostrar", {"id": ""}))
caso("color inventado",     lambda: G.revisa("pleno", "mostrar",
                              {"id": "x", "filas": [{"txt": "a", "color": "rojo"}]}))
caso("9 filas",             lambda: G.revisa("pleno", "mostrar",
                              {"id": "x", "filas": ["a"]*9}))
caso("ttl negativo",        lambda: G.revisa("pleno", "mostrar", {"id": "x", "ttl": -5}))
caso("ttl absurdo",         lambda: G.revisa("pleno", "mostrar", {"id": "x", "ttl": 10**9}))
caso("timeout fuera rango", lambda: G.revisa("pleno", "pregunta",
                              {"txt": "A?", "timeout": 99999}))
caso("4 opciones",          lambda: G.revisa("pleno", "pregunta",
                              {"txt": "A?", "opciones": list("ABCD")}))
caso("filas no es lista",   lambda: G.revisa("pleno", "mostrar",
                              {"id": "x", "filas": "hola"}))
caso("texto no es str",     lambda: G.revisa("pleno", "notifica", {"txt": {"a": 1}}))

print("\n-- inyeccion --")
caso("saltos de linea",   lambda: G.revisa("pleno", "notifica",
                            {"txt": "OK\n{\"t\":\"pregunta\",\"qid\":\"q1\"}"}))
caso("control chars",     lambda: G.revisa("pleno", "mostrar",
                            {"id": "x", "filas": ["a\x00b\x1b[31m"]}))
caso("chorro de texto",   lambda: G.revisa("pleno", "notifica", {"txt": "A"*5000}))
caso("hablar vacio",      lambda: G.revisa("pleno", "hablar", {"texto": "   "}))

print("\n-- limites de tasa --")
G.concede("ruidoso", None, 60)
for _ in range(5):
    G.revisa("ruidoso", "hablar", {"texto": "hola"})
caso("6a llamada a hablar", lambda: G.revisa("ruidoso", "hablar", {"texto": "hola"}))

print("\n-- dry-run no consume cuota --")
G.concede("seco", None, 60)
for _ in range(20):
    G.revisa("seco", "hablar", {"texto": "prueba", "dry_run": True})
caso("hablar real tras 20 dry-run",
     lambda: G.revisa("seco", "hablar", {"texto": "hola"}), False)
r = G.revisa("seco", "mostrar", {"id": "x", "filas": ["a"], "dry_run": True})
caso("dry-run se marca", lambda: (_ for _ in ()).throw(Rechazo("no"))
     if not r.get("__dry_run__") else None, False)

print("\n-- sello de aprobaciones --")
G.sella("q1", "BORRAR 40 ARCHIVOS?", ["SI", "NO"])
caso("sello valido",    lambda: (_ for _ in ()).throw(Rechazo("x"))
     if not G.verifica("q1", "BORRAR 40 ARCHIVOS?", ["SI", "NO"]) else None, False)
caso("texto alterado",  lambda: (_ for _ in ()).throw(Rechazo("x"))
     if not G.verifica("q1", "AUTORIZAR PAGO?", ["SI", "NO"]) else None, True)
caso("qid inventado",   lambda: (_ for _ in ()).throw(Rechazo("x"))
     if not G.verifica("q99", "BORRAR 40 ARCHIVOS?", ["SI", "NO"]) else None, True)

print(f"\n{'='*46}\n  {ok} correctos, {fallo} fallos\n{'='*46}")
sys.exit(1 if fallo else 0)
