"""Motor TDAH: los ocho criterios de aceptacion del spec motor-tdah.

Una jornada de ocho horas se prueba en milisegundos porque el reloj del motor
es inyectable (RNF-2). Sin eso, probar "180 minutos de foco" significa esperar
180 minutos -- es decir, en la practica, no probarlo nunca.

Mismo patron que el resto de la suite: sin pytest, sin red, importlib para
cargar el modulo fresco.
"""
import os, sys
_SRV = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_SRV)
import importlib.util


def carga(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


f = carga("foco", "nucleo/foco.py")

ok = fallo = 0


def afirma(nombre, cond, detalle=""):
    global ok, fallo
    if cond:
        ok += 1
        print(f"  ok  {nombre}")
    else:
        fallo += 1
        print(f"  FALLO {nombre}" + (f": {detalle}" if detalle else ""))


class Reloj:
    """Reloj de mentira. avanza(minutos) y la jornada vuela."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def avanza(self, minutos):
        self.t += minutos * 60
        return self.t


def motor_en_foco(reloj, app="Cursor"):
    """Lleva un motor recien creado hasta FOCO, que es el punto de partida
    de casi todas las pruebas interesantes."""
    m = f.MotorFoco(reloj=reloj)
    m.latido(app)
    reloj.avanza(11)
    m.latido(app)
    return m


print("-- 1: URGENTE atraviesa el FOCO (si esto falla, nada mas importa) --")
r = Reloj()
m = motor_en_foco(r)
afirma("entra en foco tras 10 min en la misma app", m.estado == f.FOCO, m.estado)
d = m.propone(f.Aviso("reunion en 10 min", f.URGENTE))
afirma("URGENTE pasa aunque este en foco", d == f.PASA, d)
afirma("y no se guarda en la bandeja", m.guardadas() == 0)

print("\n-- 2: AMBIENTE en foco se guarda y sale en el descanso --")
r = Reloj()
m = motor_en_foco(r)
d = m.propone(f.Aviso("titular de IA", f.AMBIENTE, clave="n1"))
afirma("AMBIENTE se guarda en foco", d == f.GUARDA, d)
d = m.propone(f.Aviso("pendiente: revisar PR", f.NORMAL, clave="p1"))
afirma("NORMAL tambien se guarda en foco", d == f.GUARDA, d)
afirma("la bandeja muestra 2 (RF-5)", m.guardadas() == 2, m.guardadas())
entregadas = m.descanso()
afirma("el descanso entrega las 2", len(entregadas) == 2, len(entregadas))
afirma("y deja la bandeja vacia", m.guardadas() == 0)
afirma("en DESCANSO ya pasa todo", m.propone(f.Aviso("x", f.AMBIENTE)) == f.PASA)

print("\n-- 3: deduplicacion, la misma clave no se acumula (RF-6) --")
r = Reloj()
m = motor_en_foco(r)
for i in range(5):
    m.propone(f.Aviso(f"build fallando (intento {i})", f.NORMAL, clave="build"))
afirma("cinco avisos con la misma clave dejan uno", m.guardadas() == 1, m.guardadas())
afirma("y el que queda es el ultimo",
       "intento 4" in m.bandeja()[0].texto, m.bandeja()[0].texto)

print("\n-- 4: deteccion de foco y de dispersion --")
r = Reloj()
m = f.MotorFoco(reloj=r)
m.latido("Blender")
r.avanza(5)
m.latido("Blender")
afirma("a los 5 min todavia no es foco", m.estado != f.FOCO, m.estado)
r.avanza(6)
m.latido("Blender")
afirma("a los 11 min si es foco", m.estado == f.FOCO, m.estado)

r2 = Reloj()
m2 = f.MotorFoco(reloj=r2)
for app in ("Slack", "Chrome", "Cursor", "Mail", "Spotify", "Finder"):
    m2.latido(app)
    r2.avanza(0.5)
afirma("6 apps en 5 min => DISPERSO", m2.estado == f.DISPERSO, m2.estado)
afirma("DISPERSO deja pasar lo NORMAL", m2.propone(f.Aviso("x", f.NORMAL)) == f.PASA)
afirma("pero retiene lo AMBIENTE", m2.propone(f.Aviso("y", f.AMBIENTE)) == f.GUARDA)
acciones = m2.tick()
afirma("y ofrece un reset (ofrece, no impone)",
       any(a["tipo"] == "reset_ofrecido" for a in acciones), acciones)

print("\n-- 5: 90 min sugiere pausa; 180 min avisa URGENTE (RF-8) --")
r = Reloj()
m = motor_en_foco(r, "Unity")
afirma("recien entrado en foco no sugiere nada", m.tick() == [], m.tick())
r.avanza(91)
m.latido("Unity")
acciones = m.tick()
afirma("a los 90 min sugiere pausa",
       any(a["tipo"] == "descanso_sugerido" for a in acciones), acciones)
afirma("la sugerencia es NORMAL, no urgente",
       acciones[0].get("urgencia") == f.NORMAL, acciones)
afirma("y no se repite en el siguiente tick",
       not any(a["tipo"] == "descanso_sugerido" for a in m.tick()))
r.avanza(95)
m.latido("Unity")
acciones = m.tick()
afirma("pasados 180 min el aviso es URGENTE",
       any(a["tipo"] == "descanso_urgente" and a["urgencia"] == f.URGENTE
           for a in acciones), acciones)

print("\n-- 6: 15 min sin senales => AUSENTE y silencio (RF-9) --")
r = Reloj()
m = motor_en_foco(r)
r.avanza(16)
acciones = m.tick()
afirma("se declara ausente", m.estado == f.AUSENTE, m.estado)
afirma("el tick lo reporta", any(a["tipo"] == "ausente" for a in acciones), acciones)
afirma("ausente guarda hasta lo URGENTE",
       m.propone(f.Aviso("reunion", f.URGENTE)) == f.GUARDA)
m.latido("Cursor")
afirma("un latido lo devuelve a LIBRE", m.estado == f.LIBRE, m.estado)
afirma("y lo guardado sigue ahi para entregarselo", m.guardadas() == 1)

print("\n-- 7: migas de pan, lo caro no es cortar sino volver (RF-7) --")
r = Reloj()
m = motor_en_foco(r, "Cursor")
m.anota("iba a probar el endpoint del clima")
r.avanza(20)
m.latido("Cursor")
m.descanso()
migas = m.migas()
afirma("recuerda la app", migas.get("app") == "Cursor", migas)
afirma("recuerda la nota",
       "endpoint del clima" in (migas.get("nota") or ""), migas)
afirma("y cuanto llevaba", migas.get("duracion_min", 0) >= 20, migas)
afirma("reanudar devuelve las migas",
       m.reanuda().get("app") == "Cursor")
afirma("tras reanudar el foco se gana de nuevo, no se hereda",
       m.estado == f.LIBRE, m.estado)

print("\n-- 8: fin de descanso abre hueco de estudio (RF-10) --")
r = Reloj()
m = motor_en_foco(r)
m.descanso()
r.avanza(11)
acciones = m.tick()
afirma("el descanso termina solo",
       any(a["tipo"] == "fin_descanso" for a in acciones), acciones)
afirma("y marca hueco para una pregunta NVIDIA",
       any(a["tipo"] == "hueco_estudio" for a in acciones), acciones)

print("\n-- extra: topes de memoria, esto corre semanas (RNF-3) --")
r = Reloj()
m = f.MotorFoco(reloj=r, cfg={"tope_bandeja": 5})
m.latido("Cursor")
r.avanza(11)
m.latido("Cursor")
for i in range(50):
    m.propone(f.Aviso(f"aviso {i}", f.AMBIENTE, clave=f"k{i}"))
afirma("la bandeja respeta su tope", m.guardadas() == 5, m.guardadas())
for _ in range(500):
    m.latido("Cursor")
afirma("el historial no crece sin limite", len(m._historial) <= 200, len(m._historial))

print("\n-- extra: snapshot para el HUD y el panel --")
r = Reloj()
m = motor_en_foco(r, "Blender")
m.propone(f.Aviso("algo", f.AMBIENTE))
s = m.snapshot()
afirma("snapshot trae estado", s["estado"] == f.FOCO, s)
afirma("snapshot trae la cuenta visible", s["guardadas"] == 1, s)
afirma("snapshot trae la app", s["app"] == "Blender", s)

print("\n" + "=" * 46)
print(f"  {ok} correctos, {fallo} fallos")
print("=" * 46)
sys.exit(1 if fallo else 0)
