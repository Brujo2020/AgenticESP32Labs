"""Multi-dispositivo: dos canales concurrentes no deben pisarse.

Reproduce en pequeno los ocho casos del diseno de la ola 1
(.kiro/specs/brain-multidispositivo/design.md): aislamiento de envios y
vistas, serializacion bajo lock, reconexion con el mismo id, que una
corrutina agonizante de una sesion vieja no mate la nueva, compatibilidad
del dispositivo sin device_id, y snapshot_todos().

Sin pytest ni asyncio.run anidados raros -- mismo patron que
test_compatibilidad_v1.py: se carga nucleo/canal.py con importlib (modulo
fresco, sin arrastrar estado de otra prueba) y se corre todo en un solo
asyncio.run(main()).
"""
import os, sys
_SRV = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_SRV)
import asyncio, json, importlib.util

def carga(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m

canal = carga("canal", "nucleo/canal.py")

ok = fallo = 0


def afirma(nombre, cond, detalle=""):
    global ok, fallo
    if cond:
        ok += 1
        print(f"  ok  {nombre}")
    else:
        fallo += 1
        print(f"  FALLO {nombre}" + (f": {detalle}" if detalle else ""))


class ESP32Falso:
    """Un socket de dispositivo simulado: registra cada envio, en orden,
    y puede simular una escritura lenta para forzar una carrera real bajo
    el lock (caso 3)."""

    def __init__(self, demora=0.0):
        self.recibido = []
        self.demora = demora

    async def send(self, dato):
        if self.demora:
            await asyncio.sleep(self.demora)
        self.recibido.append(dato)


async def main():
    REGISTRO_CLS = canal.RegistroDispositivos

    # ---------------- caso 1: aislamiento de envios ----------------
    print("\n-- 1: mostrar en A no llega a B --")
    reg = REGISTRO_CLS()
    a = reg.obtener_o_crear("stick", "sticks3")
    b = reg.obtener_o_crear("bola", "bola")
    ws_a, ws_b = ESP32Falso(), ESP32Falso()
    a.conecta(ws_a); b.conecta(ws_b)
    a.saluda({"fw": "0.1", "vistas_max": 8, "filas_max": 6, "ancho": 26})
    b.saluda({"fw": "0.1", "vistas_max": 8, "filas_max": 6, "ancho": 26})
    await a.mostrar("m1", "TEST", ["hola"])
    afirma("A recibio su vista", len(ws_a.recibido) == 1)
    afirma("B no recibio nada", len(ws_b.recibido) == 0,
           f"B recibio {ws_b.recibido}")

    # ---------------- caso 2: aislamiento de vistas ----------------
    print("\n-- 2: borrar en B no toca las vistas de A --")
    await b.mostrar("v_b", "B", ["x"])
    afirma("A conserva su vista", "m1" in a.vistas)
    await b.borrar("v_b")
    afirma("borrar en B no afecto a A", "m1" in a.vistas and "v_b" not in b.vistas)

    # ---------------- caso 3: serializacion bajo lock ----------------
    print("\n-- 3: 50 envios concurrentes a un canal, sin entrelazado --")
    reg2 = REGISTRO_CLS()
    c = reg2.obtener_o_crear("lento", "bola")
    ws_c = ESP32Falso(demora=0.001)
    c.conecta(ws_c)
    esperado = [f"msg-{i}".encode() for i in range(50)]
    await asyncio.gather(*(c.send(m) for m in esperado))
    afirma("orden preservado, sin perdidas ni duplicados",
           ws_c.recibido == esperado,
           f"{len(ws_c.recibido)} de 50, orden distinto" if ws_c.recibido != esperado else "")

    # ---------------- caso 4: reconexion con el mismo id ----------------
    print("\n-- 4: reconectar con el mismo device_id reutiliza el canal --")
    reg3 = REGISTRO_CLS()
    d1 = reg3.obtener_o_crear("stick", "sticks3")
    d1.conecta(ESP32Falso())
    await d1.mostrar("perm", "P", ["dato"])
    # Desconexion real: es lo que hace atiende() en su finally, y es a
    # proposito que limpie las vistas -- un dispositivo que se cayo y vuelve
    # arranca limpio, no con vistas fantasma que el ya no tiene en pantalla.
    d1.desconecta()
    afirma("desconectar limpia las vistas", "perm" not in d1.vistas)
    ws_reconexion = ESP32Falso()
    d2 = reg3.obtener_o_crear("stick", "sticks3")
    afirma("mismo objeto Canal tras reconectar", d1 is d2)
    afirma("sin duplicados en el registro", list(reg3._canales.keys()) == ["stick"])
    d2.conecta(ws_reconexion)
    afirma("el canal reconectado esta vivo con el socket nuevo",
           d2.vivo and d2.ws is ws_reconexion)

    # ---------------- caso 5: socket viejo no mata la reconexion --------
    print("\n-- 5: una corrutina agonizante de la sesion vieja no desconecta la nueva --")
    reg4 = REGISTRO_CLS()
    e = reg4.obtener_o_crear("stick", "sticks3")
    ws_vieja = ESP32Falso()
    e.conecta(ws_vieja)
    ws_nueva = ESP32Falso()
    e.conecta(ws_nueva)          # el dispositivo ya reconecto por su cuenta
    # Simula el 'finally' de atiende(): solo desconecta si el ws coincide.
    if e.ws is ws_vieja:
        e.desconecta()
    afirma("el canal sigue vivo con el socket nuevo", e.vivo and e.ws is ws_nueva)

    # ---------------- caso 6: sin device_id -> "bola", como siempre -----
    print("\n-- 6: dispositivo sin hola (v1) cae en 'bola' --")
    reg5 = REGISTRO_CLS()
    f = reg5.obtener_o_crear(None, "bola")   # atiende() pasa esto si no hubo 'hola'
    afirma("device_id por defecto es 'bola'", f.device_id == "bola")
    afirma("protocolo v1 (sin handshake)", f.snapshot()["protocolo"].startswith("v1"))
    ws_f = ESP32Falso()
    f.conecta(ws_f)
    r = await f.mostrar("maquina", "MAQUINA", ["CPU 10%"])
    afirma("v1 se degrada a canales fijos", ws_f.recibido and
           json.loads(ws_f.recibido[0])["t"] == "mac_reset", r)

    # ---------------- caso 7: snapshot_todos con uno vivo y otro no -----
    print("\n-- 7: snapshot_todos lista ambos con su estado real --")
    reg6 = REGISTRO_CLS()
    g1 = reg6.obtener_o_crear("bola", "bola")
    g2 = reg6.obtener_o_crear("stick", "sticks3")
    g1.conecta(ESP32Falso())
    # g2 se queda sin conectar
    snap = reg6.snapshot_todos()
    afirma("ambos aparecen", set(snap.keys()) == {"bola", "stick"})
    afirma("bola conectada, stick no", snap["bola"]["conectado"] is True
           and snap["stick"]["conectado"] is False)

    # ---------------- caso 8: por_socket con un socket desconocido -----
    print("\n-- 8: por_socket(desconocido) no revienta --")
    reg7 = REGISTRO_CLS()
    h = reg7.obtener_o_crear("bola", "bola")
    ws_h = ESP32Falso()
    h.conecta(ws_h)
    afirma("por_socket encuentra el dueño", reg7.por_socket(ws_h) is h)
    afirma("por_socket(otro) devuelve None", reg7.por_socket(object()) is None)

    # ---------------- extra: obtener() sin id resuelve al unico vivo ----
    print("\n-- extra: obtener() sin device_id, con 'bola' caida y un stick vivo --")
    reg8 = REGISTRO_CLS()
    i_bola = reg8.obtener_o_crear("bola", "bola")   # nunca conecta
    i_stick = reg8.obtener_o_crear("stick", "sticks3")
    i_stick.conecta(ESP32Falso())
    afirma("resuelve al unico canal vivo", reg8.obtener(None) is i_stick)
    i_bola.conecta(ESP32Falso())
    afirma("con la bola tambien viva, gana 'bola'", reg8.obtener(None) is i_bola)

    print(f"\n{'='*46}\n  {ok} correctos, {fallo} fallos\n{'='*46}")
    sys.exit(1 if fallo else 0)


asyncio.run(main())
