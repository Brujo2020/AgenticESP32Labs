"""Prueba extremo a extremo: puente + ESP32 simulado + cliente de control."""
import os, sys
# Las pruebas se resuelven desde su propia ubicacion, no desde el cwd: asi
# funcionan tanto con `python3 pruebas/x.py` desde servidor/ como desde la
# raiz del repo o desde el hook de un CI.
_SRV = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_SRV)
import asyncio, json, importlib.util, sys
def carga(n,p):
    s=importlib.util.spec_from_file_location(n,p); m=importlib.util.module_from_spec(s)
    s.loader.exec_module(m); return m
canal=carga("canal","nucleo/canal.py"); guardia=carga("guardia","nucleo/guardia.py")
CANAL=canal.Canal(); GUARDIA=guardia.Guardia()

recibido=[]
class ESP32Falso:   # habla protocolo v1: NO manda handshake
    async def send(self,s): recibido.append(json.loads(s))

async def main():
    CANAL.conecta(ESP32Falso())          # sin saluda() => v1
    GUARDIA.concede("cli", segundos=60)
    print("protocolo detectado:", CANAL.snapshot()["protocolo"])
    print("destinos:", CANAL.snapshot()["destinos"])

    async def cmd(fn,args):
        a=GUARDIA.revisa("cli",fn,args,33)
        if fn=="mostrar":  return await CANAL.mostrar(a["id"],a["titulo"],a["filas"],a["acento"],a["orden"],a["ttl"])
        if fn=="borrar":   return await CANAL.borrar(a["id"])
        if fn=="notifica": return await CANAL.notifica(a["txt"],a["nivel"],a["beep"])
        return None

    print("\n-- mostrar en MAQUINA --")
    recibido.clear()
    print(" ", await cmd("mostrar",{"id":"maquina","titulo":"MAQUINA",
        "filas":["CPU 34%","RAM 18/32 GB","BLENDER 41% CPU"]}))
    for m in recibido: print("   ->", m)
    assert recibido[0]["t"]=="mac_reset"
    assert [m["v"] for m in recibido[1:]]==["CPU 34%","RAM 18/32 GB","BLENDER 41% CPU"]

    print("\n-- destino invalido: mensaje util, no excepcion --")
    print(" ", (await cmd("mostrar",{"id":"unity","filas":["x"]}))[:110],"...")

    print("\n-- notifica cae a la linea de texto --")
    recibido.clear(); print(" ", await cmd("notifica",{"txt":"PR 42 aprobado","nivel":"ok"}))
    print("   ->", recibido[0]); assert recibido[0]["t"]=="texto"

    print("\n-- borrar --")
    recibido.clear(); print(" ", await cmd("borrar",{"id":"maquina"}))
    assert recibido[0]["t"]=="mac_reset"

    print("\n-- y ahora un firmware v2: mismas ordenes, vistas de verdad --")
    CANAL.saluda({"fw":"0.4.0","vistas_max":8,"filas_max":6,"ancho":26})
    print("  protocolo:", CANAL.snapshot()["protocolo"])
    recibido.clear()
    print(" ", await cmd("mostrar",{"id":"unity","titulo":"FORJA","filas":["BUILD OK"],"acento":"lime"}))
    print("   ->", {k:v for k,v in recibido[0].items() if k!="filas"})
    assert recibido[0]["t"]=="vista"

    # ── Ola 2 (protocolo v2.1): las cuatro filas de la tabla de
    # compatibilidad de PROTOCOLO.md, una por caso.
    print("\n-- tabla de compatibilidad v2.1 (protocolo v2.1, design.md ola 2) --")

    print("  fila 1: v1 (no saluda) -> canal 'bola', 3 pantallas fijas")
    c1 = canal.Canal()
    c1.conecta(ESP32Falso())
    s = c1.snapshot()
    assert s["protocolo"] == "v1 (compatibilidad)"
    assert s["device_id"] == "bola"
    print("    ok  protocolo:", s["protocolo"], "destinos:", s["destinos"])

    print("  fila 2: v2 actual (bola en produccion), hola SIN id/tipo/geometria")
    c2 = canal.Canal()
    c2.conecta(ESP32Falso())
    c2.saluda({"fw":"0.7.0","vistas_max":8,"filas_max":6,"ancho":26})
    s = c2.snapshot()
    assert s["protocolo"] == "v2"
    assert s["device_id"] == "bola"           # no cambia por no venir 'id'
    assert (s["w"], s["h"], s["entrada"]) == (240, 240, "tactil")  # valores de hoy
    assert s["servicios"] == sorted(canal.SERVICIOS)  # todos, por defecto
    print("    ok  geometria asumida:", s["w"], "x", s["h"], s["entrada"])

    print("  fila 3: v2.1 bola, con id/tipo/geometria explicitos")
    c3 = canal.Canal(device_id="bola", device_type="bola")
    c3.conecta(ESP32Falso())
    c3.saluda({"fw":"0.8.0","id":"bola","tipo":"bola","w":240,"h":240,
               "entrada":"tactil","vistas_max":8,"filas_max":6,"ancho":26,
               "servicios":["noticias","telemetria","alertas"]})
    s = c3.snapshot()
    assert (s["w"], s["h"], s["entrada"]) == (240, 240, "tactil")
    assert s["servicios"] == ["alertas", "noticias", "telemetria"]
    assert s["limites"]["opciones_max"] == 4          # tactil
    print("    ok  declarado explicito, mismos valores que la fila 2")

    print("  fila 4: v2.1 stick, 135x240, botones, solo 'alertas'")
    c4 = canal.Canal(device_id="stick", device_type="sticks3")
    c4.conecta(ESP32Falso())
    c4.saluda({"fw":"0.8.0","id":"stick","tipo":"sticks3","w":135,"h":240,
               "entrada":"botones","vistas_max":6,"filas_max":5,"ancho":21,
               "servicios":["alertas"]})
    s = c4.snapshot()
    assert (s["w"], s["h"], s["entrada"]) == (135, 240, "botones")
    assert s["servicios"] == ["alertas"]
    assert s["limites"]["opciones_max"] == 3          # botones: max 3
    assert c4.quiere("alertas") and not c4.quiere("noticias")
    print("    ok  geometria, entrada y servicios propios; noticias descartadas")

    print("\n-- evento con device_id de origen (ya no se pierde en un log) --")
    ev = c4.registra_evento("menu", 2)
    assert ev["device_id"] == "stick" and ev["fila"] == 2
    assert c4.snapshot()["ultimo_evento"]["device_id"] == "stick"
    assert c3.snapshot()["ultimo_evento"] is None     # cada canal el suyo
    print("    ok  el evento del stick no aparece en el canal de la bola")

    print("\n*** EXTREMO A EXTREMO OK ***")

asyncio.run(main())
