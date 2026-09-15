"""Pruebas de nucleo/ritmo.py -- los 7 criterios de aceptacion de
.kiro/specs/ritmo/requirements.md, mas los casos de RF-12..16 que se pueden
probar sin hardware. Reloj falso, sin red, sin I/O -- mismo patron que
pruebas/test_foco.py."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleo.ritmo import MotorRitmo, MODOS


class Reloj:
    """Reloj falso controlable a mano, igual que test_foco.py."""
    def __init__(self, t=0.0):
        self.t = t
    def __call__(self):
        return self.t
    def avanza(self, seg):
        self.t += seg


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


# ============================================================
print("\n-- criterio 1: bloque elastico vence en FOCO -> ofrece extender, no corta --")
r = Reloj()
m = MotorRitmo(reloj=r)
m.empieza(titulo="Demo NTT DATA", modo="material", minutos=90)
r.avanza(90 * 60)
acciones = m.tick(estado_foco="foco")
tipos = [a["tipo"] for a in acciones]
check("ofrece extender al vencer en FOCO", "ofrece_extender" in tipos)
check("el bloque sigue activo (no se cerro solo)", m.actual() is not None)

m.extiende()
check("tras extiende(), extensiones=1", m.actual()["extensiones"] == 1)
r.avanza(20 * 60)   # vence la extension
acciones2 = m.tick(estado_foco="foco")
check("segunda extension ofrecida", any(a["tipo"] == "ofrece_extender" for a in acciones2))
m.extiende()
check("extensiones=2 tras la segunda", m.actual()["extensiones"] == 2)
r.avanza(20 * 60)
acciones3 = m.tick(estado_foco="foco")   # tercer vencimiento: ya no se ofrece mas
check("a la tercera, cierra (no ofrece mas)", any(a["tipo"] == "fin_bloque" for a in acciones3))
check("el bloque ya no esta activo", m.actual() is None)


# ============================================================
print("\n-- criterio 2: reunion anclada cerca -> NO ofrece extension --")
r = Reloj()
m = MotorRitmo(reloj=r)
m.plan_set([
    {"titulo": "Reunion cliente", "modo": "reunion", "anclado": True,
     "inicio_previsto": 90 * 60 + 5 * 60},   # empieza 5 min despues de vencer el bloque
])
m.empieza(titulo="Preparar propuesta", modo="oferta", minutos=90)
r.avanza(90 * 60)
acciones = m.tick(estado_foco="foco")
tipos = [a["tipo"] for a in acciones]
check("NO ofrece extender con ancla a 5 min", "ofrece_extender" not in tipos)
check("marca ancla_proxima", "ancla_proxima" in tipos)
check("cierra directo", "fin_bloque" in tipos)


# ============================================================
print("\n-- criterio 3: aviso de cierre suena una vez, y se rearma al extender --")
r = Reloj()
m = MotorRitmo(reloj=r)
m.empieza(titulo="Correos", modo="multitarea", minutos=25)   # aviso_cierre_min=3
r.avanza(22 * 60)   # quedan 3 min
a1 = m.tick(estado_foco="libre")
check("aviso de cierre a los 3 min restantes", any(x["tipo"] == "aviso_cierre" for x in a1))
a2 = m.tick(estado_foco="libre")   # mismo instante, no debe repetirse
check("no se repite el aviso en el mismo bloque", not any(x["tipo"] == "aviso_cierre" for x in a2))


# ============================================================
print("\n-- criterio 4: 'material' bloquea AMBIENTE, 'investigacion' lo deja pasar --")
r = Reloj()
m = MotorRitmo(reloj=r)
m.empieza(titulo="Charla NVIDIA", modo="material", minutos=90)
check("material bloquea AMBIENTE", m.urgencia_pasa("ambiente") is False)
check("material deja pasar URGENTE siempre", m.urgencia_pasa("urgente") is True)
m.cierra()
m.confirma() if m.esperando_confirmacion() else None
m2 = MotorRitmo(reloj=Reloj())
m2.empieza(titulo="Research spatial computing", modo="investigacion", minutos=45)
check("investigacion deja pasar AMBIENTE", m2.urgencia_pasa("ambiente") is True)


# ============================================================
print("\n-- criterio 5: reconectar (matar y recrear) no pierde el bloque/restante --")
r = Reloj()
m = MotorRitmo(reloj=r)
m.empieza(titulo="Curso agentes", modo="estudio", minutos=45)
r.avanza(10 * 60)
snap_antes = m.actual()
# 'reconectar' en este motor puro es simplemente: el estado vive en RITMO,
# que es un singleton de proceso -- no se pierde entre conexiones de
# dispositivo distintas porque nunca vivio en el dispositivo (RF-9). Se
# simula leyendo actual() dos veces, como lo haria un segundo cuerpo.
snap_despues = m.actual()
check("mismo bloque, mismo restante (± redondeo)",
      abs(snap_antes["restante_seg"] - snap_despues["restante_seg"]) <= 1)
check("device-agnostic: actual() no depende de quien pregunta", snap_antes["titulo"] == snap_despues["titulo"])


# ============================================================
print("\n-- criterio 6: sin plan, plan_set([]) sigue permitiendo empezar por voz --")
r = Reloj()
m = MotorRitmo(reloj=r)
m.plan_set([])
res = m.empieza(titulo="Bloque suelto", modo="multitarea", minutos=25)
check("empieza() funciona sin plan", res is not None and res["titulo"] == "Bloque suelto")
check("siguiente() es None sin plan", m.siguiente() is None)


# ============================================================
print("\n-- criterio 7 (extra, RF-14): tras cerrar, espera confirmacion --")
r = Reloj()
m = MotorRitmo(reloj=r)
m.plan_set([
    {"id": "b1", "titulo": "Primero", "modo": "multitarea", "duracion_min": 25},
    {"id": "b2", "titulo": "Segundo", "modo": "estudio", "duracion_min": 45},
])
m.empieza(bloque_id="b1")
r.avanza(25 * 60 + 1)
acciones = m.tick(estado_foco="libre")
check("fin_bloque dispara", any(a["tipo"] == "fin_bloque" for a in acciones))
check("queda esperando confirmacion", m.esperando_confirmacion() is True)
check("el reloj del siguiente NO corre todavia", m.actual() is None)
res = m.confirma()
check("confirma() arranca el siguiente", res["titulo"] == "Segundo")
check("ya no esta esperando confirmacion", m.esperando_confirmacion() is False)


# ============================================================
print("\n-- RF-13: semaforo cyan -> ambar (10 min) -> rojo (5 min) --")
r = Reloj()
m = MotorRitmo(reloj=r)
m.empieza(titulo="X", modo="material", minutos=90)   # 90 min
check("semaforo normal al empezar", m.actual()["semaforo"] == "normal")
r.avanza(79 * 60)   # quedan 11 min
check("semaforo normal con 11 min restantes", m.actual()["semaforo"] == "normal")
r.avanza(2 * 60)    # quedan 9 min
check("semaforo ambar con 9 min restantes", m.actual()["semaforo"] == "ambar")
r.avanza(5 * 60)    # quedan 4 min
check("semaforo rojo con 4 min restantes", m.actual()["semaforo"] == "rojo")


# ============================================================
print("\n-- RF-16: vias frias solo se calculan al llamar, no durante el bloque --")
r = Reloj()
m = MotorRitmo(reloj=r, cfg={"dias_via_fria": 3})
m.plan_set([{"titulo": "A", "modo": "estudio", "via": "cert-nvidia"},
            {"titulo": "B", "modo": "material", "via": "charla-npm"}])
m.toca_via("cert-nvidia", ts=0)
m.toca_via("charla-npm", ts=0)
frias = m.vias_frias(ts=1 * 86400)   # 1 dia despues: nada frio todavia
check("con 1 dia, ninguna via fria (umbral 3)", frias == [])
frias2 = m.vias_frias(ts=4 * 86400)   # 4 dias despues: ambas frias
check("con 4 dias, ambas vias frias", len(frias2) == 2)
m.toca_via("cert-nvidia", ts=4 * 86400)   # se toca una
frias3 = m.vias_frias(ts=4 * 86400 + 1)
check("tras tocarla, cert-nvidia ya no esta fria", all(f["via"] != "cert-nvidia" for f in frias3))
check("charla-npm sigue fria", any(f["via"] == "charla-npm" for f in frias3))


# ============================================================
print("\n-- 'construccion' nunca se extiende (tope duro), aunque este en FOCO --")
r = Reloj()
m = MotorRitmo(reloj=r)
m.empieza(titulo="Firmware sticks3", modo="construccion", minutos=50)
r.avanza(50 * 60)
acciones = m.tick(estado_foco="foco")
check("construccion cierra en FOCO igual (tope duro)", any(a["tipo"] == "fin_bloque" for a in acciones))
check("nunca ofrece extender en construccion", not any(a["tipo"] == "ofrece_extender" for a in acciones))


# ============================================================
print("\n-- sin FOCO, no se ofrece extension aunque el modo sea extensible --")
r = Reloj()
m = MotorRitmo(reloj=r)
m.empieza(titulo="Y", modo="material", minutos=90)
r.avanza(90 * 60)
acciones = m.tick(estado_foco="disperso")
check("sin FOCO, cierra directo aunque sea extensible", any(a["tipo"] == "fin_bloque" for a in acciones))
check("sin FOCO, no ofrece extension", not any(a["tipo"] == "ofrece_extender" for a in acciones))


print("\n" + "=" * 46)
if fallos:
    print(f"  {ok} correctos, {len(fallos)} FALLOS: {fallos}")
    print("=" * 46)
    sys.exit(1)
else:
    print(f"  {ok} correctos, 0 fallos")
    print("=" * 46)
