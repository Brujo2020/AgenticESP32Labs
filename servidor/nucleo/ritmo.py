"""
Ritmo — el copiloto de la jornada. `MotorFoco` (nucleo/foco.py) infiere EN QUE
ESTADO estoy; este modulo responde QUE TOCA AHORA, CUANTO QUEDA Y QUE VIENE.

Ver .kiro/specs/ritmo/{requirements,design}.md y
docs/investigacion/estado-del-arte-tdah.md para el porque de cada regla --
este modulo es la implementacion literal de esos documentos, no una
reinterpretacion.

DISENO DELIBERADO — este modulo no hace I/O, mismo patron que nucleo/foco.py
-----------------------------------------------------------------------
Ni red, ni websocket, ni disco. Entran senales (el estado de foco, ordenes
humanas), salen acciones. El reloj es inyectable: una jornada de horas se
prueba en milisegundos.

`tick()` recibe el estado de MotorFoco como ARGUMENTO, no lo importa (D-2 de
design.md): mantiene este motor puro y testeable con un estado falso, y deja
explicita la dependencia real entre los dos motores.
"""
import time

# ---------------------------------------------------------------- modos
#
# Cada modo trae el patron de tiempo que la evidencia recomienda para ese
# tipo de trabajo (estado-del-arte-tdah.md §3 y §9-mejora-1). 'extensible'
# es si RF-5 puede ofrecer +20 min cuando vence en pleno FOCO; 'construccion'
# es el UNICO con tope duro (es el mas propenso al "una cosita mas").
MODOS = {
    "material":       {"duracion_min": 90, "aviso_cierre_min": 10, "extensible": True,
                        "deja_pasar_ambiente": False},
    "oferta":         {"duracion_min": 50, "aviso_cierre_min": 10, "extensible": True,
                        "deja_pasar_ambiente": False},
    "investigacion":  {"duracion_min": 45, "aviso_cierre_min": 5,  "extensible": True,
                        "deja_pasar_ambiente": True},
    "construccion":   {"duracion_min": 50, "aviso_cierre_min": 5,  "extensible": False,
                        "deja_pasar_ambiente": False},
    "arquitectura":   {"duracion_min": 90, "aviso_cierre_min": 10, "extensible": True,
                        "deja_pasar_ambiente": False},
    "estudio":        {"duracion_min": 45, "aviso_cierre_min": 5,  "extensible": True,
                        "deja_pasar_ambiente": False},
    "multitarea":     {"duracion_min": 25, "aviso_cierre_min": 3,  "extensible": False,
                        "deja_pasar_ambiente": False},
    "reunion":        {"duracion_min": None, "aviso_cierre_min": 5, "extensible": False,
                        "deja_pasar_ambiente": False},
    "descanso":       {"duracion_min": 10, "aviso_cierre_min": 1,  "extensible": False,
                        "deja_pasar_ambiente": False},
}

MODOS_VALIDOS = tuple(MODOS)

ESTADOS_BLOQUE = ("pendiente", "activo", "cerrado", "saltado")

# RF-13: semaforo de agotamiento. Umbrales absolutos en minutos restantes,
# independientes de la duracion del bloque -- ver el porque en
# requirements.md RF-13 (un color que cambia se percibe sin leer).
UMBRAL_AMBAR_MIN = 10
UMBRAL_ROJO_MIN = 5

# RF-5: como mucho dos extensiones de 20 min antes de cerrar si o si.
MAX_EXTENSIONES = 2
MINUTOS_EXTENSION = 20

# RF-2 tabla: un modo endurece la politica de SuperPower solo en un sentido
# (nunca deja pasar menos que 'nucleo/foco.py' para un URGENTE). 'ambiente'
# depende de 'deja_pasar_ambiente' del modo; 'normal' y 'urgente' del modo
# nunca se endurecen mas alla de lo que ya decide MotorFoco.
from nucleo.foco import URGENTE, NORMAL, AMBIENTE, PASA, GUARDA  # noqa: E402


class MotorRitmo:
    """Maquina del bloque activo + plan del dia + vias.

    Uso desde el puente:

        RITMO.plan_set([...])
        RITMO.empieza(modo="material", titulo="Demo NTT DATA", minutos=90)
        for accion in RITMO.tick(estado_foco=MOTOR.estado):
            ...
        RITMO.extiende()   # si el humano acepta la oferta
        RITMO.confirma()   # RF-14: arranca el siguiente bloque
    """

    def __init__(self, reloj=time.monotonic, cfg=None):
        cfg = cfg or {}
        self._reloj = reloj
        self._modos = {**MODOS, **(cfg.get("modos") or {})}
        self.minutos_ancla_bloquea_extension = int(cfg.get("minutos_ancla_bloquea_extension", 10))
        self.dias_via_fria = int(cfg.get("dias_via_fria", 3))

        self._plan = []               # lista de Bloque, orden del dia
        self._activo = None           # Bloque en curso, o None
        self._siguiente_propuesto = None   # Bloque a la vista en 'esperando_confirmacion'
        self._esperando_confirmacion = False
        self._ofrecido_extension = False
        self._ultimo_toque_via = {}   # via -> epoch (time.time(), no monotonic: sobrevive reinicios)
        self._qid = 0

    # ---------------------------------------------------------------- plan

    def plan_set(self, bloques: list) -> None:
        """Reemplaza el plan del dia. NUNCA toca el bloque activo (RF-9 de
        ritmo: reconstruir el plan a mitad de un bloque no debe cortarlo)."""
        self._plan = [self._normaliza(b) for b in bloques]

    def _normaliza(self, b: dict) -> dict:
        self._qid += 1
        modo = b.get("modo") if b.get("modo") in self._modos else "multitarea"
        cfg_modo = self._modos[modo]
        return {
            "id": b.get("id") or f"b{self._qid}",
            "titulo": str(b.get("titulo", ""))[:60],
            "modo": modo,
            "via": b.get("via") or modo,
            "duracion_min": int(b.get("duracion_min") or cfg_modo["duracion_min"] or 25),
            "anclado": bool(b.get("anclado", modo == "reunion")),
            "inicio_previsto": b.get("inicio_previsto"),
            "origen": b.get("origen", "manual"),
            "estado": "pendiente",
            "extensiones": 0,
            "_inicio_real": None,
        }

    def plan(self) -> list:
        return list(self._plan)

    # ------------------------------------------------------- bloque activo

    def empieza(self, bloque_id: str = None, titulo: str = None, modo: str = None,
                minutos: int = None, via: str = None, ts=None) -> dict:
        """Arranca un bloque YA (RF-4: siempre por orden explicita — voz,
        boton, o un bloque anclado que llega a su hora). Si `bloque_id`
        coincide con uno del plan, se usa ese; si no, se crea uno suelto
        (la voz puede decir "empieza hiperfoco cincuenta minutos" sin que
        estuviera planificado)."""
        ahora = ts if ts is not None else self._reloj()
        origen = None
        if bloque_id:
            origen = next((b for b in self._plan if b["id"] == bloque_id), None)
        if origen is not None:
            b = dict(origen)
        else:
            b = self._normaliza({
                "titulo": titulo or "Bloque", "modo": modo or "multitarea",
                "duracion_min": minutos, "via": via, "origen": "voz",
            })
        if minutos:
            b["duracion_min"] = int(minutos)
        b["estado"] = "activo"
        b["_inicio_real"] = ahora
        b["extensiones"] = 0
        self._activo = b
        self._esperando_confirmacion = False
        self._siguiente_propuesto = None
        self._ofrecido_extension = False
        self._ultimo_toque_via[b["via"]] = time.time()
        # La copia activa vive separada del plan (RF-9: reconstruir el plan
        # a mitad de un bloque no debe cortarlo) -- pero la entrada de
        # origen en self._plan SI debe dejar de estar 'pendiente', o
        # siguiente() la seguiria devolviendo para siempre.
        if origen is not None:
            origen["estado"] = "activo"
        return self.actual()

    def extiende(self, minutos: int = MINUTOS_EXTENSION, ts=None) -> dict:
        """El humano acepta la oferta de RF-5 (o la pide directo por voz).
        No comprueba de nuevo las condiciones: quien decide es la persona,
        el motor solo ofrecio."""
        if self._activo is None:
            return {"error": "no hay bloque activo"}
        self._activo["duracion_min"] += int(minutos)
        self._activo["extensiones"] += 1
        self._ofrecido_extension = False
        return self.actual()

    def _sincroniza_plan(self, bloque_id: str, estado: str) -> None:
        """Refleja en self._plan el desenlace de un bloque que vino de ahi
        (los sueltos por voz, con id nuevo, simplemente no hacen match)."""
        for p in self._plan:
            if p["id"] == bloque_id:
                p["estado"] = estado
                break

    def salta(self, ts=None) -> dict:
        """Descarta el bloque activo sin marcarlo cerrado por vencimiento
        (RF-8 de voz: 'saltate esto')."""
        if self._activo is not None:
            self._activo["estado"] = "saltado"
            self._sincroniza_plan(self._activo["id"], "saltado")
        self._activo = None
        self._ofrecido_extension = False
        return self._prepara_siguiente(ts)

    def cierra(self, ts=None) -> dict:
        """Cierra el bloque activo AHORA (antes de vencer, o aceptando el
        cierre en vez de la extension ofrecida)."""
        if self._activo is not None:
            self._activo["estado"] = "cerrado"
            self._sincroniza_plan(self._activo["id"], "cerrado")
        self._activo = None
        self._ofrecido_extension = False
        return self._prepara_siguiente(ts)

    def confirma(self, ts=None) -> dict:
        """RF-14: arranca el bloque que estaba esperando confirmacion. El
        reloj de ese bloque empieza AQUI, no cuando termino el anterior."""
        if self._siguiente_propuesto is None:
            return {"error": "no hay nada esperando confirmacion"}
        b = self._siguiente_propuesto
        self._siguiente_propuesto = None
        self._esperando_confirmacion = False
        return self.empieza(bloque_id=b["id"] if b["id"] in {p["id"] for p in self._plan} else None,
                             titulo=b["titulo"], modo=b["modo"],
                             minutos=b["duracion_min"], via=b["via"], ts=ts)

    def _prepara_siguiente(self, ts=None) -> dict:
        """Tras cerrar/saltar: deja el siguiente A LA VISTA, esperando
        confirmacion (RF-14). No lo arranca solo."""
        sig = self.siguiente()
        if sig is not None:
            self._siguiente_propuesto = sig
            self._esperando_confirmacion = True
        else:
            self._siguiente_propuesto = None
            self._esperando_confirmacion = False
        return {"esperando_confirmacion": self._esperando_confirmacion,
                "siguiente": sig}

    # -------------------------------------------------------------- lectura

    def actual(self) -> dict | None:
        if self._activo is None:
            return None
        ahora = self._reloj()
        transcurrido = ahora - self._activo["_inicio_real"]
        restante = max(0, self._activo["duracion_min"] * 60 - transcurrido)
        duracion_total = self._activo["duracion_min"] * 60
        pct = 0 if duracion_total <= 0 else max(0.0, min(1.0, restante / duracion_total))
        restante_min = restante / 60
        if restante_min <= UMBRAL_ROJO_MIN:
            semaforo = "rojo"
        elif restante_min <= UMBRAL_AMBAR_MIN:
            semaforo = "ambar"
        else:
            semaforo = "normal"
        return {
            **{k: v for k, v in self._activo.items() if not k.startswith("_")},
            "restante_seg": int(restante),
            "pct_restante": round(pct, 3),
            "semaforo": semaforo,
        }

    def siguiente(self) -> dict | None:
        """El proximo bloque pendiente del plan, en orden. No incluye
        cerrados/saltados/el activo."""
        for b in self._plan:
            if b["estado"] == "pendiente":
                return dict(b)
        return None

    def esperando_confirmacion(self) -> bool:
        return self._esperando_confirmacion

    # --------------------------------------------------------- interrupcion

    def urgencia_pasa(self, urgencia: str) -> bool:
        """RF-2: ¿el modo del bloque activo deja pasar esta urgencia?

        Solo puede ENDURECER (PASA de MotorFoco -> GUARDA aqui), nunca
        ablandar: un URGENTE que MotorFoco dejaria pasar en FOCO sigue
        pasando siempre (la fila URGENTE de POLITICA no se toca desde
        aqui). Ver design.md 'Integracion con SuperPower'."""
        if urgencia == URGENTE:
            return True
        if self._activo is None:
            return True
        if urgencia == AMBIENTE:
            return self._modos.get(self._activo["modo"], {}).get("deja_pasar_ambiente", False)
        # NORMAL: el modo no anade restriccion extra sobre lo que ya decide
        # MotorFoco (esa es su fila propia de la tabla RF-4 de superpower).
        return True

    # --------------------------------------------------------- temporizador

    def tick(self, estado_foco: str, ts=None) -> list[dict]:
        """Vencimientos del bloque activo. Devuelve acciones; el motor no
        ejecuta nada — eso es responsabilidad del puente (mismo contrato
        que MotorFoco.tick())."""
        ahora = ts if ts is not None else self._reloj()
        acciones = []

        if self._activo is None:
            return acciones

        b = self._activo
        transcurrido = ahora - b["_inicio_real"]
        duracion_seg = b["duracion_min"] * 60
        restante = duracion_seg - transcurrido
        cfg_modo = self._modos.get(b["modo"], MODOS["multitarea"])

        # Aviso de cierre (RF-6), una sola vez por bloque -- se rearma solo
        # si se extiende (la extension resetea 'duracion_min', asi que el
        # calculo de 'restante' ya lo refleja; el flag vive en el bloque).
        aviso_key = "_avisado_cierre_extension_%d" % b["extensiones"]
        if (not b.get(aviso_key)
                and 0 < restante <= cfg_modo["aviso_cierre_min"] * 60):
            b[aviso_key] = True
            acciones.append({"tipo": "aviso_cierre", "minutos_restantes": cfg_modo["aviso_cierre_min"]})

        if restante > 0:
            return acciones   # no vencio todavia

        # --- vencio ---
        ancla_proxima = self._ancla_bloquea_extension(ahora)
        if ancla_proxima:
            acciones.append({"tipo": "ancla_proxima"})

        if (cfg_modo["extensible"] and not b["anclado"] and estado_foco == "foco"
                and b["extensiones"] < MAX_EXTENSIONES and not ancla_proxima):
            if not self._ofrecido_extension:
                self._ofrecido_extension = True
                acciones.append({
                    "tipo": "ofrece_extender",
                    "minutos": MINUTOS_EXTENSION,
                    "extensiones_usadas": b["extensiones"],
                })
            return acciones   # se queda activo, esperando extiende()/cierra()

        # Sin extension posible: cierra de verdad.
        b["estado"] = "cerrado"
        self._sincroniza_plan(b["id"], "cerrado")
        self._activo = None
        self._ofrecido_extension = False
        self._prepara_siguiente(ahora)
        acciones.append({
            "tipo": "fin_bloque",
            "siguiente": self._siguiente_propuesto,
        })
        return acciones

    def _ancla_bloquea_extension(self, ahora) -> bool:
        """RF-3: si el siguiente pendiente es anclado y esta cerca, no se
        ofrece extension aunque el estado sea FOCO."""
        sig = self.siguiente()
        if not sig or not sig.get("anclado") or not sig.get("inicio_previsto"):
            return False
        return sig["inicio_previsto"] - ahora <= self.minutos_ancla_bloquea_extension * 60

    # -------------------------------------------------------------- vias

    def toca_via(self, via: str, ts=None) -> None:
        self._ultimo_toque_via[str(via)] = ts if ts is not None else time.time()

    def vias_frias(self, ts=None) -> list[dict]:
        """RF-16: vias que llevan mas de `dias_via_fria` sin tocarse. Se
        calcula SOLO cuando se llama (al planificar), nunca durante un
        bloque activo -- el puente decide cuando invocar esto."""
        ahora = ts if ts is not None else time.time()
        vias_conocidas = {b["via"] for b in self._plan} | set(self._ultimo_toque_via)
        frias = []
        for via in sorted(vias_conocidas):
            ultimo = self._ultimo_toque_via.get(via)
            dias = 999 if ultimo is None else (ahora - ultimo) / 86400
            if dias >= self.dias_via_fria:
                frias.append({"via": via, "dias": round(dias, 1)})
        return sorted(frias, key=lambda x: -x["dias"])

    # -------------------------------------------------------------- estado

    def snapshot(self) -> dict:
        return {
            "activo": self.actual(),
            "siguiente": self.siguiente(),
            "esperando_confirmacion": self._esperando_confirmacion,
            "plan_len": len(self._plan),
            "vias_frias": self.vias_frias(),
        }


# Instancia unica del proceso, mismo patron que MOTOR en foco.py: el ritmo
# es de la PERSONA, no de un dispositivo por el que en ese momento se hable.
RITMO = MotorRitmo()
