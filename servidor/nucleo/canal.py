"""
Canal hacia un dispositivo conectado (bola blanca, M5StickS3, ...).

Un objeto `Canal` por dispositivo, todo su estado aislado (socket, vistas,
preguntas pendientes, limites, estado reportado). El registro
`RegistroDispositivos` es lo unico que sabe cuantos dispositivos hay: nadie
mas debe guardar una referencia larga a un Canal salvo quien lo esta usando
en el momento.

Ola 1 (brain-multidispositivo): antes de esto existia un singleton global
`CANAL` para un solo dispositivo. El intento de hacerlo multi-dispositivo
dejo un alias de modulo (`CANAL = REGISTRO._default`) que quedaba
desincronizado en cuanto el registro reasignaba cual era "el default" -- el
camino de voz seguia hablando con el canal viejo. Aqui no se arregla el
alias: se elimina el concepto de canal global. Quien necesite un canal, lo
pide al registro con un device_id, o lo recibe como argumento.
"""
import asyncio, json, logging, time
from dataclasses import dataclass, field

log = logging.getLogger("canal")

# Limites por defecto. El handshake del firmware los sobreescribe: son el
# tamano de sus buffers estaticos, no una preferencia.
LIMITES = {"vistas_max": 8, "filas_max": 6, "ancho": 26}

# Servicios periodicos que un dispositivo puede recibir. Por defecto todo
# canal los quiere todos (comportamiento historico, previo a la ola 2). Un
# handshake v2.1 con 'servicios' explicito lo estrecha -- tipicamente el
# Stick, que solo pide 'alertas' porque es de bolsillo (design.md ola 2).
SERVICIOS = {"noticias", "telemetria", "alertas"}

# Tipos de entrada validos en el campo 'entrada' del hola v2.1.
ENTRADAS = {"tactil", "botones"}

# Cuantas opciones caben en una 'pregunta', derivado del tipo de entrada
# (ola 2, design.md): con dos botones fisicos (KEY1/KEY2) navegar mas de 3
# opciones es un mal rato; con tactil se puede desplazar una lista algo mas
# larga. Es la razon tecnica, no arbitraria, de por que este numero varia
# por dispositivo y no es una constante global.
OPCIONES_MAX = {"botones": 3, "tactil": 4}

# Modo compatibilidad con el firmware v1 (el que no manda handshake).
# Ese firmware no sabe de vistas declarativas, pero SI tiene tres pantallas
# que el servidor rellena por canales fijos. Mapeando las vistas a esos
# canales, un agente puede pintar en el HUD sin reflashear nada.
#
#   SENALES  <- canal 'noticia'   (5 lineas de 33 caracteres)
#   MAQUINA  <- canal 'mac'       (7 lineas)
#   FORJA    <- canal 'creativo'  (7 lineas)
#
# No es tan bueno como el protocolo v2: son tres huecos con nombre fijo en
# vez de ocho vistas con titulo propio. Pero funciona hoy.
CANALES_V1 = {
    "senales":  ("noticia",  "noticias_reset", 5, "SENALES"),
    "maquina":  ("mac",      "mac_reset",      7, "MAQUINA"),
    "forja":    ("creativo", "creativo_reset", 7, "FORJA"),
}
LIMITES_V1 = {"vistas_max": 3, "filas_max": 5, "ancho": 33}

ACENTOS = {"cyan", "magenta", "lime", "amber", "ice", "blood", "grey", "white"}
NIVELES = {"info", "ok", "warn", "error"}

# Mismo orden que ajustes_t.tema en components/ajustes/include/ajustes.h --
# cambiar uno sin el otro desincroniza el panel del firmware.
ACENTOS_ORDEN = ["cyan", "magenta", "lime", "amber", "ice", "blood", "grey", "white"]

# device_id del dispositivo que no manda identidad explicita en el hola (o
# que ni siquiera saluda: firmware v1). Es la bola blanca, hoy la unica
# placa en produccion -- ver PLAN.md ley 1. NO es un nombre generico tipo
# "default": un id fijo y con nombre es lo que permite que /api/dispositivo
# resuelva sin ambiguedad a "el dispositivo de siempre" cuando no se pide
# ninguno en concreto.
DEVICE_ID_POR_DEFECTO = "bola"


@dataclass
class Canal:
    ws: object = None
    fw: str = ""
    device_id: str = DEVICE_ID_POR_DEFECTO
    device_type: str = "bola"  # "bola" o "sticks3"
    limites: dict = field(default_factory=lambda: dict(LIMITES))
    servicios: set = field(default_factory=lambda: set(SERVICIOS))
    # Geometria y tipo de entrada declarados en el hola v2.1. Los valores por
    # defecto son los de la bola blanca de hoy -- ley 1: un dispositivo que no
    # declara nada (firmware v1, o v2 sin estos campos) se comporta exactamente
    # como se comportaba antes de la ola 2.
    w: int = 240
    h: int = 240
    entrada: str = "tactil"
    ultimo_evento: dict = field(default_factory=dict)
    vistas: dict = field(default_factory=dict)
    _pendientes: dict = field(default_factory=dict)   # qid -> Future
    _resueltas: dict = field(default_factory=dict)    # qid -> resultado archivado
    _vence: dict = field(default_factory=dict)        # qid -> instante limite
    _opciones: dict = field(default_factory=dict)     # qid -> etiquetas
    _qid: int = 0
    estado: dict = field(default_factory=dict)
    # Serializa TODA escritura hacia ESTE dispositivo. Ver Canal.send().
    _lock_envio: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ---------------- conexion ----------------
    @property
    def vivo(self) -> bool:
        return self.ws is not None

    def conecta(self, ws):
        self.ws = ws
        log.info("dispositivo '%s' (%s) conectado", self.device_id, self.device_type)

    def desconecta(self):
        self.ws = None
        # Nadie va a contestar: liberar a quien espere, no dejarlo colgado
        for fut in self._pendientes.values():
            if not fut.done():
                fut.set_result(-1)
        self._pendientes.clear()
        self.vistas.clear()
        log.info("dispositivo '%s' desconectado", self.device_id)

    @property
    def v2(self) -> bool:
        """El firmware anuncio soporte del protocolo v2 en el handshake."""
        return bool(self.fw)

    def saluda(self, data: dict):
        """Procesa el handshake 'hola'. El firmware DECLARA, el servidor
        RESPETA -- no hay ida y vuelta de negociacion (design.md ola 2).

        Todo campo es opcional y cada uno cae a su valor de hoy si falta:
        es lo que hace que la bola en produccion (que aun manda el hola v2
        sin 'entrada'/'servicios'/geometria) siga funcionando identico.
        """
        self.fw = data.get("fw", "?")
        for k in LIMITES:
            if isinstance(data.get(k), int) and data[k] > 0:
                self.limites[k] = data[k]

        if isinstance(data.get("w"), int) and data["w"] > 0:
            self.w = data["w"]
        if isinstance(data.get("h"), int) and data["h"] > 0:
            self.h = data["h"]

        entrada = data.get("entrada")
        if entrada in ENTRADAS:
            self.entrada = entrada
        # Deriva el limite de opciones del tipo de entrada YA resuelto, no del
        # payload crudo: asi un dispositivo que solo manda 'entrada' sin nada
        # mas ya tiene el numero correcto sin necesitar mandarlo el tambien.
        self.limites["opciones_max"] = OPCIONES_MAX.get(self.entrada, 3)

        servicios = data.get("servicios")
        if isinstance(servicios, list) and servicios:
            # Se estrecha a la interseccion con lo que el servidor conoce: un
            # firmware de una version futura que pida un servicio que este
            # servidor todavia no tiene no debe dejarlo suscrito a nada por
            # error tipografico silencioso.
            pedidos = {s for s in servicios if isinstance(s, str)}
            conocidos = pedidos & SERVICIOS
            if conocidos:
                self.servicios = conocidos

        log.info("handshake '%s' fw=%s geometria=%dx%d entrada=%s "
                 "limites=%s servicios=%s",
                 self.device_id, self.fw, self.w, self.h, self.entrada,
                 self.limites, sorted(self.servicios))

    def quiere(self, servicio: str) -> bool:
        """¿Este canal quiere recibir el servicio periodico dado?

        Ola 2: lee 'self.servicios', que 'saluda()' estrecha si el hola
        declaro un campo 'servicios' explicito. Sin ese campo (firmware v1,
        o v2 sin declararlo), todo canal nace queriendo todos los servicios
        -- el comportamiento que ya existia con un solo dispositivo.
        """
        return servicio in self.servicios

    def registra_evento(self, id: str, fila) -> dict:
        """Guarda el ultimo toque/pulsacion con SU device_id de origen, para
        que una tool MCP pueda responder 'en el dispositivo donde se toco'
        (ola 2, mcps/dispositivo.py) en vez de perderlo en un log de texto."""
        self.ultimo_evento = {
            "device_id": self.device_id, "id": id, "fila": fila, "ts": time.time(),
        }
        return self.ultimo_evento

    async def send(self, dato):
        """UNICA puerta de escritura hacia ESTE dispositivo. Serializa con
        un lock propio del canal.

        Hace falta porque hay varios productores escribiendo a la vez sobre
        el mismo socket: el bucle que atiende la voz (que manda el audio de
        la respuesta troceado en decenas de frames seguidos), la tarea de
        telemetria (cada 5 s), la de noticias, y los comandos que llegan por
        el canal de control. 'websockets' NO admite escrituras concurrentes:
        si una tarea escribe en mitad del envio de otra, los frames se
        intercalan, el stream queda corrupto y el dispositivo cierra la
        conexion.

        Con dos dispositivos, este lock vive en el Canal y no en un objeto
        compartido: lo que tarde el Stick en su turno no bloquea nada de lo
        que le toque a la bola, y viceversa.
        """
        ws = self.ws
        if ws is None:
            raise RuntimeError(f"no hay dispositivo conectado ('{self.device_id}')")
        async with self._lock_envio:
            await ws.send(dato)

    async def _envia(self, obj: dict):
        if not self.ws:
            raise RuntimeError(f"no hay dispositivo conectado ('{self.device_id}')")
        await self.send(json.dumps(obj, ensure_ascii=False))

    # ---------------- vistas ----------------
    def _fila(self, f) -> dict:
        """Normaliza una fila y la trunca al ancho real del panel."""
        if isinstance(f, str):
            f = {"txt": f}
        txt = str(f.get("txt", "")).upper()[: self.limites["ancho"]]
        out = {"txt": txt, "color": f.get("color", "white")}
        if f.get("badge"):
            out["badge"] = str(f["badge"])[:3]
        return out

    async def _mostrar_v1(self, id: str, filas: list) -> str:
        """Pinta en el firmware v1 usando los canales que ya entiende."""
        destino = CANALES_V1.get(id)
        if not destino:
            return ("Este firmware solo admite tres destinos: "
                    + ", ".join(f"'{k}' (pantalla {v[3]})" for k, v in CANALES_V1.items())
                    + ". Usa uno de esos como 'id', o actualiza el firmware al "
                      "protocolo v2 para tener ocho vistas con titulo libre.")
        canal, reset, maxf, pantalla = destino
        await self._envia({"t": reset, "v": ""})
        n = 0
        for f in filas[:maxf]:
            txt = (f if isinstance(f, str) else str(f.get("txt", "")))
            txt = txt.upper()[: LIMITES_V1["ancho"]]
            if txt:
                await self._envia({"t": canal, "v": txt})
                n += 1
        self.vistas[id] = {"id": id, "filas": filas[:maxf], "modo": "v1"}
        return (f"Pintadas {n} lineas en la pantalla {pantalla} del HUD "
                f"(modo compatibilidad v1). Navega con los botones laterales.")

    async def mostrar(self, id: str, titulo: str, filas: list,
                      acento: str = "cyan", orden: int = 99, ttl: int = 0) -> str:
        # Firmware sin protocolo v2: se degrada a los canales fijos en vez de
        # fallar. Mejor tres pantallas hoy que ocho despues de reflashear.
        if not self.v2:
            return await self._mostrar_v1(str(id)[:15].lower(), filas or [])
        if acento not in ACENTOS:
            acento = "cyan"
        if len(self.vistas) >= self.limites["vistas_max"] and id not in self.vistas:
            return (f"No caben mas vistas (maximo {self.limites['vistas_max']}). "
                    f"Borra una con hud_borrar. Activas: {sorted(self.vistas)}")
        vista = {
            "t": "vista",
            "id": str(id)[:15],
            "titulo": str(titulo).upper()[:10],
            "acento": acento,
            "orden": int(orden),
            "filas": [self._fila(f) for f in (filas or [])][: self.limites["filas_max"]],
            "ttl": int(ttl),
        }
        await self._envia(vista)
        self.vistas[vista["id"]] = vista
        n = len(vista["filas"])
        return f"Vista '{vista['id']}' en pantalla con {n} fila{'s' if n != 1 else ''}."

    async def borrar(self, id: str) -> str:
        id = str(id)[:15]
        if not self.v2:
            d = CANALES_V1.get(id.lower())
            if d:
                await self._envia({"t": d[1], "v": ""})
                self.vistas.pop(id.lower(), None)
                return f"Pantalla {d[3]} vaciada."
            return f"No existe el destino '{id}'."
        if id not in self.vistas:
            return f"No existe la vista '{id}'. Activas: {sorted(self.vistas)}"
        await self._envia({"t": "vista_borra", "id": id})
        self.vistas.pop(id, None)
        return f"Vista '{id}' eliminada."

    # ---------------- pregunta bloqueante ----------------
    async def pregunta(self, txt: str, opciones: list, timeout: int = 30) -> dict:
        """Bloquea hasta que el humano toca, o hasta que vence el plazo.

        El timeout no es opcional por diseno: si el usuario se fue por un cafe,
        el bucle de herramientas del agente no puede quedarse colgado.
        """
        # Ola 2: el limite ya no es un '3' fijo, sale de OPCIONES_MAX segun
        # el tipo de entrada declarado en el hola (limites['opciones_max'],
        # que 'saluda()' rellena; 3 si nadie saludo todavia -- mismo valor
        # que existia antes de la ola 2, cero cambio de comportamiento).
        tope = self.limites.get("opciones_max", 3)
        opciones = [str(o).upper()[:10] for o in (opciones or ["SI", "NO"])][:tope]
        self._qid += 1
        qid = f"q{self._qid}"
        fut = asyncio.get_running_loop().create_future()
        self._pendientes[qid] = fut

        await self._envia({"t": "pregunta", "qid": qid,
                           "txt": str(txt).upper()[: self.limites["ancho"] * 2],
                           "opciones": opciones, "timeout": int(timeout)})
        t0 = time.time()
        try:
            idx = await asyncio.wait_for(fut, timeout=timeout + 2)
        except asyncio.TimeoutError:
            idx = -1
        finally:
            self._pendientes.pop(qid, None)

        seg = round(time.time() - t0, 1)
        if idx < 0 or idx >= len(opciones):
            return {"respondido": False, "opcion": None, "indice": -1, "segundos": seg}
        return {"respondido": True, "opcion": opciones[idx], "indice": idx, "segundos": seg}

    async def pregunta_async(self, txt: str, opciones: list, timeout: int = 30) -> dict:
        """Lanza la pregunta y devuelve el identificador sin esperar.

        Es el patron Tasks de MCP 2026-07-28: retener un tool call mientras una
        persona decide es justo lo que esa extension vino a eliminar.
        """
        # Ola 2: el limite ya no es un '3' fijo, sale de OPCIONES_MAX segun
        # el tipo de entrada declarado en el hola (limites['opciones_max'],
        # que 'saluda()' rellena; 3 si nadie saludo todavia -- mismo valor
        # que existia antes de la ola 2, cero cambio de comportamiento).
        tope = self.limites.get("opciones_max", 3)
        opciones = [str(o).upper()[:10] for o in (opciones or ["SI", "NO"])][:tope]
        self._qid += 1
        qid = f"q{self._qid}"
        fut = asyncio.get_running_loop().create_future()
        self._pendientes[qid] = fut
        self._vence[qid] = time.time() + timeout
        self._opciones[qid] = opciones
        await self._envia({"t": "pregunta", "qid": qid,
                           "txt": str(txt).upper()[: self.limites["ancho"] * 2],
                           "opciones": opciones, "timeout": int(timeout)})
        return {"qid": qid, "opciones": opciones, "timeout": int(timeout)}

    def consulta(self, qid: str) -> dict:
        """Estado de una pregunta lanzada con pregunta_async."""
        fut = self._pendientes.get(qid)
        if fut is None:
            r = self._resueltas.get(qid)
            return r or {"estado": "desconocido", "respondido": False,
                         "opcion": None, "indice": -1}
        if not fut.done():
            resta = int(self._vence.get(qid, 0) - time.time())
            if resta <= 0:
                self.resuelve(qid, -1)
                return self.consulta(qid)
            return {"estado": "pendiente", "segundos": max(0, resta)}
        return self._resueltas.get(qid, {"estado": "listo", "respondido": False})

    def resuelve(self, qid: str, opcion: int):
        fut = self._pendientes.get(qid)
        if fut and not fut.done():
            fut.set_result(int(opcion))
        # Se archiva el resultado: quien consulte despues debe poder leerlo
        # aunque el future ya se haya recogido.
        ops = self._opciones.get(qid, [])
        i = int(opcion)
        self._resueltas[qid] = (
            {"estado": "listo", "respondido": True, "opcion": ops[i],
             "indice": i, "segundos": 0}
            if 0 <= i < len(ops) else
            {"estado": "listo", "respondido": False, "opcion": None,
             "indice": -1, "segundos": 0})
        self._pendientes.pop(qid, None)
        self._vence.pop(qid, None)

    # ---------------- ajustes remotos ----------------
    async def configurar(self, brillo: int = None, volumen: int = None,
                         tema_hud: str = None, efectos: bool = None) -> str:
        """Aplica brillo/volumen/tema/efectos en el ESP32 AHORA MISMO y los
        deja persistidos en su NVS (el propio firmware los guarda al
        recibir 'config'). Requiere firmware con el manejador de 'config'
        en components/voice/voice.c -- si el dispositivo es viejo, el
        mensaje simplemente no hace nada (se ignora, no rompe nada)."""
        if not self.v2:
            return ("Este firmware no anuncio protocolo v2 en el handshake: "
                    "no puede recibir ajustes remotos. Actualiza el firmware.")
        cuerpo = {"t": "config"}
        if brillo is not None:
            cuerpo["brillo"] = max(0, min(100, int(brillo)))
        if volumen is not None:
            cuerpo["volumen"] = max(0, min(100, int(volumen)))
        if tema_hud is not None:
            if tema_hud not in ACENTOS_ORDEN:
                return f"tema_hud debe ser uno de: {', '.join(ACENTOS_ORDEN)}"
            cuerpo["tema"] = ACENTOS_ORDEN.index(tema_hud)
        if efectos is not None:
            cuerpo["efectos"] = bool(efectos)
        await self._envia(cuerpo)
        return f"Ajustes enviados al ESP32: {cuerpo}"

    # Pantallas fijas del carrusel, en el mismo orden que hud_screen_t
    # (components/hud/include/hud.h) y NOMBRES_FIJAS en hud.c. El indice ES
    # el contrato con el firmware: reordenar esta lista sin tocar el C
    # rompe la correspondencia.
    PANTALLAS = ["nucleo", "reloj", "clima", "voz", "chat",
                 "noticias", "mac", "creativo", "ajustes", "sistema"]

    async def pantallas(self, activas: list, orden: list = None) -> str:
        """Configura que pantallas fijas se ven en el carrusel y en que orden.

        'activas' y 'orden' aceptan nombres (de PANTALLAS) o indices. Se
        persiste en la NVS del ESP32: sobrevive al reinicio del dispositivo.
        """
        if not self.v2:
            return ("Este firmware no anuncio protocolo v2: no puede recibir "
                    "la configuracion del carrusel. Actualiza el firmware.")

        def a_indice(x):
            if isinstance(x, int):
                return x if 0 <= x < len(self.PANTALLAS) else None
            n = str(x).strip().lower()
            return self.PANTALLAS.index(n) if n in self.PANTALLAS else None

        idx_activas = [i for i in (a_indice(x) for x in (activas or [])) if i is not None]
        if not idx_activas:
            return ("No se puede dejar el carrusel vacio: manda al menos una "
                    f"pantalla. Validas: {', '.join(self.PANTALLAS)}")
        # 'ajustes' siempre se manda activa: es la puerta de vuelta desde el
        # propio dispositivo si alguien se deja fuera todo lo demas. El
        # firmware la protege igual, pero mejor no mentirle al usuario sobre
        # lo que se guardo.
        i_ajustes = self.PANTALLAS.index("ajustes")
        if i_ajustes not in idx_activas:
            idx_activas.append(i_ajustes)

        cuerpo = {"t": "pantallas", "activas": sorted(set(idx_activas))}
        if orden:
            idx_orden = [i for i in (a_indice(x) for x in orden) if i is not None]
            vistos, limpio = set(), []
            for i in idx_orden:
                if i not in vistos:
                    vistos.add(i)
                    limpio.append(i)
            cuerpo["orden"] = limpio
        await self._envia(cuerpo)
        nombres = [self.PANTALLAS[i] for i in cuerpo["activas"]]
        return f"Carrusel actualizado. Visibles: {', '.join(nombres)}."

    async def wifi(self, accion: str, ssid: str, password: str = "") -> str:
        """Guarda o borra una red WiFi en la memoria del ESP32.

        Tiene efecto en el PROXIMO arranque, no ahora mismo: cambiar de red en
        caliente cortaria justo la conexion por la que llego la orden, y desde
        el panel no se distinguiria "se guardo bien" de "la clave estaba mal".

        La contrasena viaja hasta la placa pero no vuelve: el dispositivo solo
        reporta los SSID guardados.
        """
        if not self.v2:
            return ("Este firmware no admite configurar el WiFi por el "
                    "protocolo. Actualiza el firmware.")
        if accion not in ("guardar", "borrar"):
            return "accion debe ser 'guardar' o 'borrar'"
        if not ssid:
            return "hace falta el nombre de la red (ssid)"
        cuerpo = {"t": "wifi", "accion": accion, "ssid": ssid}
        if accion == "guardar":
            cuerpo["pass"] = password or ""
        await self._envia(cuerpo)
        if accion == "borrar":
            return f"Red '{ssid}' borrada del dispositivo."
        return (f"Red '{ssid}' guardada en el dispositivo. Se usara la proxima "
                f"vez que arranque y no encuentre una mejor.")

    async def reiniciar(self) -> str:
        """Reinicia el ESP32 (esp_restart). Irreversible en el sentido de que
        corta la sesion de voz en curso -- usar con cuidado, avisar antes."""
        if not self.v2:
            return ("Este firmware no anuncio protocolo v2: no tiene manejador "
                    "de reinicio remoto. Actualiza el firmware.")
        await self._envia({"t": "reiniciar"})
        return "Orden de reinicio enviada. El ESP32 se reconectara solo en unos segundos."

    # ---------------- otros ----------------
    async def notifica(self, txt: str, nivel: str = "info", beep: bool = False) -> str:
        if nivel not in NIVELES:
            nivel = "info"
        # El firmware v1 no tiene banda de aviso, pero si una linea de texto
        # de estado que se ve en la pantalla VOZ. Se usa esa.
        if not self.v2:
            await self._envia({"t": "texto", "v": str(txt).upper()[:40]})
            return f"Aviso mostrado en la pantalla VOZ: {txt}"
        await self._envia({"t": "notifica", "nivel": nivel, "beep": bool(beep),
                           "txt": str(txt).upper()[: self.limites["ancho"] * 2]})
        return f"Notificado ({nivel}): {txt}"

    async def habla(self, texto: str) -> str:
        """Marca el texto para que el puente lo sintetice y lo envie."""
        await self._envia({"t": "estado", "v": "speaking"})
        self._por_hablar = texto
        return f"Se dira en voz alta: {texto}"

    def snapshot(self) -> dict:
        return {
            "conectado": self.vivo,
            "device_id": self.device_id,
            "device_type": self.device_type,
            "protocolo": "v2" if self.v2 else "v1 (compatibilidad)",
            "destinos": (sorted(self.vistas) if self.v2
                         else [f"{k} -> pantalla {v[3]}" for k, v in CANALES_V1.items()]),
            "firmware": self.fw or None,
            "vistas_activas": sorted(self.vistas),
            "limites": self.limites,
            "servicios": sorted(self.servicios),
            # Ola 2: geometria y entrada explicitas, para que el panel y las
            # tools MCP sepan que aparato tienen delante sin adivinar por el
            # device_type ("es 'sticks3' o realmente tiene botones?").
            "w": self.w,
            "h": self.h,
            "entrada": self.entrada,
            "ultimo_evento": self.ultimo_evento or None,
            **self.estado,
        }


class RegistroDispositivos:
    """Registro de todos los canales concurrentes (bola blanca, Stick, ...).

    Unica estructura: `_canales`, un dict device_id -> Canal. No hay un
    objeto "default" aparte -- esa doble contabilidad (un dict MAS un
    puntero especial) fue justo lo que en el intento anterior dejo un alias
    de modulo (`CANAL`) apuntando a un canal que dejo de ser el que importaba.
    """

    def __init__(self):
        self._canales: dict[str, "Canal"] = {}

    def _crea(self, device_id: str, device_type: str) -> "Canal":
        c = Canal(device_id=device_id, device_type=device_type)
        self._canales[device_id] = c
        return c

    def obtener_o_crear(self, device_id: str, device_type: str = "bola") -> "Canal":
        """Usado por el puente al recibir una conexion de dispositivo (o su
        handshake). Crea el canal si es la primera vez que se ve ese id."""
        device_id = device_id or DEVICE_ID_POR_DEFECTO
        c = self._canales.get(device_id)
        if c is None:
            c = self._crea(device_id, device_type)
        else:
            c.device_type = device_type
        return c

    def obtener(self, device_id: str = None) -> "Canal":
        """Resuelve un canal para leer/mandar un comando (MCP, panel).

        Regla explicita, sin heuristicas ocultas:
          - con device_id: ese canal (se crea vacio -- no vivo -- si no
            existia, para poder responder "no conectado" en vez de fallar).
          - sin device_id: el canal 'bola' si esta vivo; si no, el unico
            canal vivo si hay exactamente uno; si no, 'bola' (vivo o no).
        """
        if device_id:
            c = self._canales.get(device_id)
            return c if c is not None else self._crea(device_id, "bola")

        bola = self._canales.get(DEVICE_ID_POR_DEFECTO)
        if bola is not None and bola.vivo:
            return bola
        vivos = self.vivos()
        if len(vivos) == 1:
            return vivos[0]
        if bola is not None:
            return bola
        return self._crea(DEVICE_ID_POR_DEFECTO, "bola")

    def por_socket(self, ws) -> "Canal | None":
        """Dueño del socket dado, o None si no es ningun canal registrado
        (p.ej. una conexion de control, que no pasa por este registro)."""
        for c in self._canales.values():
            if c.ws is ws:
                return c
        return None

    def vivos(self) -> list:
        return [c for c in self._canales.values() if c.vivo]

    def desconectar(self, device_id: str):
        c = self._canales.get(device_id)
        if c is not None:
            c.desconecta()

    def snapshot_todos(self) -> dict:
        return {dev_id: c.snapshot() for dev_id, c in self._canales.items()}


REGISTRO_DISPOSITIVOS = RegistroDispositivos()
