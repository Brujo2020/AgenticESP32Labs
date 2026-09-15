"""
Motor TDAH — infiere el estado de atencion y decide si vale la pena hablar.

Es el corazon del producto (MISION.md §2b). La idea que lo separa de un
pomodoro: **un temporizador no sabe si estoy concentrado**. Avisa a los 25
minutos tanto si estoy en el mejor tramo del dia como si llevo media hora
saltando entre ocho ventanas. Para una cabeza con TDAH eso es peor que nada:
rompe el foco bueno y no detecta la dispersion.

Aqui el orden se invierte: primero se infiere el estado a partir de senales de
actividad reales, y solo despues se decide si abrir la boca.

DISENO DELIBERADO — este modulo no hace I/O
-------------------------------------------
Ni red, ni WebSocket, ni disco, ni logging de efectos. Entran senales, salen
decisiones. Quien las ejecute (el puente) es problema de otro fichero.

Dos razones:

1. Se puede probar una jornada de ocho horas en milisegundos, porque el reloj
   es inyectable. Sin eso, probar "180 minutos de foco" significa esperar 180
   minutos o no probarlo -- y en la practica significa no probarlo.
2. La politica de interrupcion es la regla de negocio mas delicada del
   producto. Mezclarla con el transporte es como acabas sin saber por que el
   cacharro hablo en mitad de una reunion.

PRIVACIDAD, QUE AQUI ES LEY Y NO OPCION
---------------------------------------
De las senales de actividad entra metadato agregado: el NOMBRE de la app y un
contador de pulsaciones. Nunca el contenido: ni lo tecleado, ni titulos de
documento, ni audio. Si algun dia alguien quiere pasar por aqui el titulo de
la ventana, la respuesta es no: el valor de este aparato depende de que se le
pueda tener delante sin pensarlo dos veces.
"""
import time
from collections import OrderedDict


# ---------------------------------------------------------------- estados

LIBRE = "libre"          # sin sesion de foco; el paso esta abierto
FOCO = "foco"            # concentracion sostenida; se protege
DISPERSO = "disperso"    # saltos erraticos; se ofrece un reset
DESCANSO = "descanso"    # pausa deliberada; se entrega lo guardado
AUSENTE = "ausente"      # no hay nadie; silencio total

ESTADOS = (LIBRE, FOCO, DISPERSO, DESCANSO, AUSENTE)


# --------------------------------------------------------------- urgencias

URGENTE = "urgente"      # reunion en 10 min, aprobacion de un agente
NORMAL = "normal"        # pendiente del dia, alerta de herramienta
AMBIENTE = "ambiente"    # noticias, tendencias

URGENCIAS = (URGENTE, NORMAL, AMBIENTE)

# --------------------------------------------------------------- decisiones

PASA = "pasa"            # entregar ahora
GUARDA = "guarda"        # a la bandeja, se entrega en el proximo hueco
DESCARTA = "descarta"    # ni ahora ni luego


# La tabla de RF-4, escrita como dato y no como una escalera de ifs: es la
# regla que mas se va a discutir y conviene poder leerla de un vistazo.
#
# La fila que manda sobre todas: URGENTE atraviesa FOCO. Un motor que se traga
# un aviso de reunion por proteger la concentracion es un motor roto.
POLITICA = {
    URGENTE:  {LIBRE: PASA,   FOCO: PASA,   DISPERSO: PASA,   DESCANSO: PASA, AUSENTE: GUARDA},
    NORMAL:   {LIBRE: PASA,   FOCO: GUARDA, DISPERSO: PASA,   DESCANSO: PASA, AUSENTE: GUARDA},
    AMBIENTE: {LIBRE: PASA,   FOCO: GUARDA, DISPERSO: GUARDA, DESCANSO: PASA, AUSENTE: GUARDA},
}


class Aviso:
    """Algo que el sistema querria decir. Puede que no sea el momento.

    `clave` existe para RF-6: dos avisos con la misma clave no se acumulan, el
    nuevo pisa al viejo. Sin esto, cuarenta minutos de foco producen una
    avalancha al salir -- que es justo la experiencia que este motor existe
    para evitar.
    """

    __slots__ = ("texto", "urgencia", "clave", "origen", "ts")

    def __init__(self, texto, urgencia=NORMAL, clave=None, origen=None, ts=None):
        if urgencia not in URGENCIAS:
            urgencia = NORMAL
        self.texto = str(texto)
        self.urgencia = urgencia
        # Sin clave explicita, el propio texto hace de clave: dos veces el
        # mismo titular no son dos titulares.
        self.clave = clave or f"{origen or ''}:{self.texto}"
        self.origen = origen
        self.ts = ts

    def __repr__(self):
        return f"<Aviso {self.urgencia} {self.texto!r}>"


class MotorFoco:
    """Maquina de estados de atencion.

    Uso desde el puente:

        motor.latido("Cursor", teclas=120)       # senal de actividad
        d = motor.propone(Aviso("...", NORMAL))  # ¿lo digo ahora?
        if d == PASA: await canal.notifica(...)

        for accion in motor.tick():              # temporizadores
            ...

    El reloj se inyecta para las pruebas (RNF-2). En produccion es
    `time.monotonic`, que no salta con los cambios de hora del sistema -- algo
    que importa cuando mides tramos de 90 minutos.
    """

    def __init__(self, reloj=time.monotonic, cfg=None):
        cfg = cfg or {}
        self._reloj = reloj

        # --- umbrales, todos en segundos y todos configurables
        self.umbral_foco = int(cfg.get("umbral_foco", 10 * 60))
        self.max_foco = int(cfg.get("max_foco", 90 * 60))
        self.foco_daninio = int(cfg.get("foco_daninio", 180 * 60))
        self.umbral_ausente = int(cfg.get("umbral_ausente", 15 * 60))
        self.ventana_dispersion = int(cfg.get("ventana_dispersion", 5 * 60))
        self.apps_dispersion = int(cfg.get("apps_dispersion", 6))
        self.duracion_descanso = int(cfg.get("duracion_descanso", 10 * 60))
        # RNF-3: topes duros. Este proceso corre semanas.
        self.tope_bandeja = int(cfg.get("tope_bandeja", 50))
        self.tope_historial = int(cfg.get("tope_historial", 200))

        self.estado = LIBRE
        self._desde = self._reloj()        # cuando empezo el estado actual
        self._app = None                   # app en la que se sostiene el foco
        self._app_desde = self._reloj()
        self._ultimo_latido = self._reloj()
        self._historial = []               # [(ts, app)] para detectar dispersion
        # OrderedDict: la bandeja conserva el orden de llegada y ademas deduplica
        # por clave en O(1). Las dos cosas importan (RF-5 y RF-6).
        self._bandeja = OrderedDict()
        self._migas = {}
        self._sugerido_descanso = False    # para no repetir el aviso cada tick
        self._avisado_daninio = False
        self._fin_descanso = None

    # ------------------------------------------------------------- señales

    def latido(self, app, teclas=0, ts=None):
        """Una senal de actividad: 'sigo aqui, y estoy en esta app'.

        Solo entra el NOMBRE de la app y un contador. Ver la nota de
        privacidad de la cabecera.
        """
        ahora = ts if ts is not None else self._reloj()
        self._ultimo_latido = ahora
        app = str(app) if app else "?"

        self._historial.append((ahora, app))
        if len(self._historial) > self.tope_historial:
            del self._historial[: len(self._historial) - self.tope_historial]

        if app != self._app:
            self._app = app
            self._app_desde = ahora

        # Volver de la ausencia es un evento, no un cambio silencioso: el
        # puente querra entregar lo acumulado.
        if self.estado == AUSENTE:
            self._cambia(LIBRE, ahora)
            # El foco se gana, no se hereda. Sin esta linea, volver del almuerzo
            # y tocar la misma app reentra en FOCO al instante, porque
            # `_app_desde` sigue apuntando a antes de irse -- y el motor
            # empezaria a proteger una concentracion que no existe todavia.
            # Lo encontro test_foco.py, caso 6.
            self._app_desde = ahora

        self._reevalua(ahora)
        return self.estado

    def _apps_recientes(self, ahora):
        corte = ahora - self.ventana_dispersion
        return {a for (t, a) in self._historial if t >= corte}

    def _reevalua(self, ahora):
        """Infiere el estado. No toca DESCANSO: ese es deliberado del humano."""
        if self.estado == DESCANSO:
            return

        distintas = self._apps_recientes(ahora)

        # La dispersion manda sobre el foco: si esta saltando entre ocho
        # ventanas, da igual cuanto lleve "en" la ultima.
        if len(distintas) >= self.apps_dispersion:
            if self.estado != DISPERSO:
                self._cambia(DISPERSO, ahora)
            return

        sostenido = ahora - self._app_desde
        if sostenido >= self.umbral_foco:
            if self.estado != FOCO:
                self._cambia(FOCO, ahora)
        elif self.estado in (FOCO, DISPERSO):
            # Cambio de app estando en foco: no se rompe el estado todavia,
            # cambiar de ventana un momento es normal. Se degrada a LIBRE solo
            # si la nueva app no aguanta.
            if self.estado == DISPERSO and len(distintas) < self.apps_dispersion:
                self._cambia(LIBRE, ahora)

    def _cambia(self, nuevo, ahora):
        anterior = self.estado
        if anterior == FOCO and nuevo != FOCO:
            # RF-7: al salir del foco se congela el contexto, que es lo que
            # hara barato volver.
            self._migas = {
                "app": self._app,
                "duracion_min": int((ahora - self._desde) // 60),
                "nota": self._migas.get("nota"),
            }
        self.estado = nuevo
        self._desde = ahora
        if nuevo != FOCO:
            self._sugerido_descanso = False
            self._avisado_daninio = False

    # ------------------------------------------------------------ decision

    def propone(self, aviso, ts=None):
        """¿Se dice ahora, se guarda, o se tira? Aplica RF-4."""
        ahora = ts if ts is not None else self._reloj()
        self._vence_ausencia(ahora)

        decision = POLITICA[aviso.urgencia][self.estado]
        if decision == GUARDA:
            aviso.ts = ahora
            # Dedup (RF-6): la misma clave se reemplaza, no se acumula.
            if aviso.clave in self._bandeja:
                del self._bandeja[aviso.clave]
            self._bandeja[aviso.clave] = aviso
            while len(self._bandeja) > self.tope_bandeja:
                # Se descarta lo mas viejo: si lleva ahi tanto, ya no es noticia.
                self._bandeja.popitem(last=False)
        return decision

    def guardadas(self):
        """Cuantas hay en la bandeja. RF-5: el HUD lo muestra y por eso confio."""
        return len(self._bandeja)

    def bandeja(self):
        return list(self._bandeja.values())

    def vacia_bandeja(self):
        """Devuelve lo acumulado y lo limpia. Lo llama el puente en un hueco."""
        pendientes = list(self._bandeja.values())
        self._bandeja.clear()
        return pendientes

    # --------------------------------------------------------- temporizadores

    def tick(self, ts=None):
        """Vencimientos. Devuelve una lista de acciones para que el puente las
        ejecute; el motor no ejecuta nada por si mismo.

        Cada accion es un dict con al menos {"tipo": ...}.
        """
        ahora = ts if ts is not None else self._reloj()
        acciones = []

        if self._vence_ausencia(ahora):
            acciones.append({"tipo": "ausente"})
            return acciones   # si no hay nadie, no hay mas que decir

        # Fin del descanso: es el hueco perfecto para estudiar (RF-10).
        if self.estado == DESCANSO and self._fin_descanso and ahora >= self._fin_descanso:
            self._cambia(LIBRE, ahora)
            self._fin_descanso = None
            acciones.append({"tipo": "fin_descanso"})
            acciones.append({"tipo": "hueco_estudio"})
            return acciones

        if self.estado == FOCO:
            sostenido = ahora - self._desde
            # RF-8: el hiperfoco daninio es el UNICO caso en que el motor
            # interrumpe el foco por iniciativa propia.
            if sostenido >= self.foco_daninio and not self._avisado_daninio:
                self._avisado_daninio = True
                acciones.append({
                    "tipo": "descanso_urgente",
                    "urgencia": URGENTE,
                    "minutos": int(sostenido // 60),
                    "texto": f"{int(sostenido // 60)} min sin parar. Levantate.",
                })
            elif sostenido >= self.max_foco and not self._sugerido_descanso:
                self._sugerido_descanso = True
                acciones.append({
                    "tipo": "descanso_sugerido",
                    "urgencia": NORMAL,
                    "minutos": int(sostenido // 60),
                    "texto": f"{int(sostenido // 60)} min de foco. ¿Pausa?",
                })

        if self.estado == DISPERSO:
            # Se OFRECE (RF-3). No se impone: a nadie le sirve un cacharro que
            # regana por cambiar de ventana.
            acciones.append({
                "tipo": "reset_ofrecido",
                "texto": "Vas saltando. ¿Ordenamos?",
            })

        return acciones

    def _vence_ausencia(self, ahora):
        if self.estado == AUSENTE:
            return False
        if ahora - self._ultimo_latido >= self.umbral_ausente:
            self._cambia(AUSENTE, ahora)
            return True
        return False

    # ------------------------------------------------------ control humano

    def descanso(self, ts=None, duracion=None):
        """Descanso deliberado. Se entrega lo guardado."""
        ahora = ts if ts is not None else self._reloj()
        self._cambia(DESCANSO, ahora)
        self._fin_descanso = ahora + (duracion or self.duracion_descanso)
        return self.vacia_bandeja()

    def reanuda(self, ts=None):
        ahora = ts if ts is not None else self._reloj()
        self._cambia(LIBRE, ahora)
        self._fin_descanso = None
        self._app_desde = ahora     # el foco se gana de nuevo, no se hereda
        return self.migas()

    def anota(self, nota):
        """Deja una miga explicita: 'iba a probar el endpoint del clima'."""
        self._migas["nota"] = str(nota)

    def migas(self):
        """RF-7: donde estaba. Con TDAH lo caro no es cortar, es volver."""
        return dict(self._migas)

    # ------------------------------------------------------------ inspeccion

    def snapshot(self):
        ahora = self._reloj()
        return {
            "estado": self.estado,
            "minutos_en_estado": int((ahora - self._desde) // 60),
            "app": self._app,
            "guardadas": self.guardadas(),
            "migas": self.migas(),
            "apps_recientes": sorted(self._apps_recientes(ahora)),
        }


# Instancia unica del proceso, mismo patron que REGISTRO_DISPOSITIVOS en
# canal.py. Es deliberado que SEA una unica instancia y no una por Canal:
# el foco es de la PERSONA, no del dispositivo por el que en este momento
# le habla el agente. Si mañana hay tres cuerpos, siguen compartiendo un
# unico estado de atencion -- lo contrario (un MotorFoco por canal) haria
# que cambiar de cuerpo "reiniciara" el foco, que es exactamente el error
# que la doctrina de roles (MISION.md §4bis) existe para evitar.
MOTOR = MotorFoco()
