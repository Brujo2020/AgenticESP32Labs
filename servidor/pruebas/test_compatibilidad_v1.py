"""Prueba extremo a extremo: puente + ESP32 simulado + cliente de control."""
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
    print("\n*** EXTREMO A EXTREMO OK ***")

asyncio.run(main())
