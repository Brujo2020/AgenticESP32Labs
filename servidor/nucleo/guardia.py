"""
Guardia: la unica frontera de confianza entre el agente y el dispositivo.

Motivacion. El patron que usamos —un puente en el host que expone el ESP32
como herramientas MCP— es correcto en la forma y era ingenuo en el fondo:
cualquier cosa que el modelo pidiera llegaba al cacharro tal cual. Eso importa
mas de lo normal aqui porque hud_preguntar es un canal de *aprobacion*: si un
agente con prompt injection puede falsificar la pregunta, corrompe justo el
mecanismo que existe para dar seguridad.

El diseno sigue el modelo de Device Context Protocol (arXiv 2605.26159):
el bridge del host valida y rechaza ANTES de que ningun byte salga hacia el
dispositivo, con capacidades acotadas y caducas, limites de tasa, validacion
de rango y tipo, y dry-run. El paper mide que estas primitivas no cuestan
latencia observable: 15.60 ms frente a 15.59 ms sin ellas.

Lo que esto NO es: una defensa perfecta contra prompt injection. El propio
paper mide 78 % de rechazo, no 100 %. Es reduccion de superficie, no garantia.
"""
import hashlib, hmac, logging, os, re, secrets, time
from dataclasses import dataclass, field

log = logging.getLogger("guardia")

# Clave de sesion: se regenera en cada arranque del puente. No se persiste
# a proposito, para que un token filtrado no sobreviva a un reinicio.
_SECRETO = os.getenv("HUD_SECRET", "").encode() or secrets.token_bytes(32)

# Texto que llega a una pantalla de 26 caracteres: alfabeto acotado. Rechazar
# control chars evita que alguien inyecte saltos o secuencias raras en el JSON
# que luego el firmware trocea.
_TEXTO_OK = re.compile(r"^[A-Za-z0-9 ÁÉÍÓÚÑÜáéíóúñü.,:;!?()\[\]/%+\-_'\"@#*=<>]*$")

ACENTOS = {"cyan", "magenta", "lime", "amber", "ice", "blood", "grey", "white"}
NIVELES = {"info", "ok", "warn", "error"}

# Capacidades. Un agente no necesita todas: se le concede el minimo.
# 'administrar' es aparte de 'ver'/'avisar': toca hardware (brillo, volumen,
# reinicio) en vez de solo pintar en pantalla -- que algo pueda mostrar texto
# no significa que deba poder reiniciar el dispositivo.
CAPACIDADES = {"ver", "avisar", "hablar", "preguntar", "estado", "administrar"}

# Que capacidad exige cada comando
EXIGE = {
    "mostrar":  "ver",
    "borrar":   "ver",
    "notifica": "avisar",
    "hablar":   "hablar",
    "pregunta": "preguntar",
    "pregunta_async": "preguntar",
    "consulta": "estado",
    "estado":   "estado",
    "configurar": "administrar",
    "reiniciar":  "administrar",
    "pantallas":  "administrar",
}

# Limites de tasa por comando: (llamadas, ventana en segundos).
# 'hablar' es el mas restringido: es el unico que produce ruido fisico en la
# habitacion y por tanto el mas molesto si un agente entra en bucle.
# 'reiniciar' a 2 cada 5 minutos: es destructivo para la sesion de voz en
# curso, no hay razon legitima para pedirlo en bucle.
LIMITES = {
    "mostrar":  (30, 60),
    "borrar":   (30, 60),
    "notifica": (10, 60),
    "hablar":   (5,  60),
    "pregunta": (6,  60),
    "pregunta_async": (6, 60),
    "consulta": (120, 60),
    "estado":   (60, 60),
    "configurar": (20, 60),
    "reiniciar":  (2, 300),
    "pantallas":  (20, 60),
}


class Rechazo(Exception):
    """El comando no sale al dispositivo. El mensaje se devuelve al modelo."""


@dataclass
class Concesion:
    """Capacidades otorgadas a un cliente, acotadas y con caducidad."""
    sujeto: str
    capacidades: set
    expira: float
    def viva(self) -> bool:
        return time.time() < self.expira
    def permite(self, cap: str) -> bool:
        return self.viva() and cap in self.capacidades


@dataclass
class Guardia:
    concesiones: dict = field(default_factory=dict)
    _tasa: dict = field(default_factory=dict)
    _preguntas: dict = field(default_factory=dict)   # qid -> firma
    dry_run: bool = False

    # ---------------- capacidades ----------------
    def concede(self, sujeto: str, capacidades=None, segundos: int = 3600) -> Concesion:
        caps = set(capacidades or CAPACIDADES) & CAPACIDADES
        c = Concesion(sujeto, caps, time.time() + segundos)
        self.concesiones[sujeto] = c
        log.info("concedido a '%s': %s durante %ds", sujeto, sorted(caps), segundos)
        return c

    def revoca(self, sujeto: str):
        self.concesiones.pop(sujeto, None)

    def _autoriza(self, sujeto: str, cmd: str):
        cap = EXIGE.get(cmd)
        if cap is None:
            raise Rechazo(f"Comando desconocido '{cmd}'.")
        c = self.concesiones.get(sujeto)
        if c is None:
            raise Rechazo(f"'{sujeto}' no tiene ninguna capacidad concedida.")
        if not c.viva():
            raise Rechazo(f"La concesion de '{sujeto}' caduco. Pide una nueva.")
        if not c.permite(cap):
            raise Rechazo(
                f"'{sujeto}' no tiene la capacidad '{cap}', necesaria para {cmd}. "
                f"Tiene: {sorted(c.capacidades)}.")

    # ---------------- tasa ----------------
    def _tasa_ok(self, sujeto: str, cmd: str):
        n, ventana = LIMITES.get(cmd, (20, 60))
        clave = (sujeto, cmd)
        ahora = time.time()
        hist = [t for t in self._tasa.get(clave, []) if ahora - t < ventana]
        if len(hist) >= n:
            espera = int(ventana - (ahora - hist[0])) + 1
            raise Rechazo(
                f"Limite de tasa para '{cmd}': {n} por {ventana}s. "
                f"Reintenta en {espera}s. Si necesitas mas, replantea el enfoque: "
                f"repetir la misma llamada no va a funcionar.")
        hist.append(ahora)
        self._tasa[clave] = hist

    # ---------------- validacion de forma ----------------
    def _texto(self, v, campo: str, largo: int) -> str:
        if v is None:
            return ""
        if not isinstance(v, str):
            raise Rechazo(f"'{campo}' debe ser texto, llego {type(v).__name__}.")
        if len(v) > largo * 3:
            raise Rechazo(
                f"'{campo}' mide {len(v)} caracteres y el maximo util es {largo}. "
                f"La pantalla es de 240 px: resume en origen, no mandes texto "
                f"que se va a cortar.")
        if not _TEXTO_OK.match(v):
            raise Rechazo(f"'{campo}' tiene caracteres no imprimibles o de control.")
        return v[:largo]

    def _entero(self, v, campo: str, lo: int, hi: int, por_defecto: int) -> int:
        if v is None:
            return por_defecto
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise Rechazo(f"'{campo}' debe ser un entero, llego {v!r}.")
        if not (lo <= n <= hi):
            raise Rechazo(f"'{campo}'={n} fuera de rango [{lo}, {hi}].")
        return n

    # ---------------- comprobacion principal ----------------
    def revisa(self, sujeto: str, cmd: str, args: dict, ancho: int = 26) -> dict:
        """Autoriza, valida y normaliza. Devuelve los args saneados o lanza.

        Los args que salen de aqui son los unicos que deberian llegar al
        dispositivo. Quien llame no debe usar los originales.
        """
        self._autoriza(sujeto, cmd)
        a = dict(args or {})
        # dry-run: valida sin ejecutar y sin consumir cuota. Es la via para que
        # un agente compruebe que su llamada es correcta antes de que produzca
        # un efecto fisico. En DCP es un bit del propio wire format.
        seco = bool(a.pop("dry_run", False)) or self.dry_run
        if not seco:
            self._tasa_ok(sujeto, cmd)

        if cmd == "mostrar":
            ident = self._texto(a.get("id"), "id", 15)
            if not re.fullmatch(r"[a-z0-9_-]{1,15}", ident or ""):
                raise Rechazo(
                    "'id' debe ser minusculas, digitos, guion o guion bajo "
                    "(1-15). Es una clave estable, no un titulo.")
            filas = a.get("filas") or []
            if not isinstance(filas, list):
                raise Rechazo("'filas' debe ser una lista.")
            if len(filas) > 6:
                raise Rechazo(f"Llegaron {len(filas)} filas y caben 6. "
                              f"Prioriza: la pantalla no crece.")
            limpias = []
            for i, f in enumerate(filas):
                if isinstance(f, str):
                    f = {"txt": f}
                if not isinstance(f, dict):
                    raise Rechazo(f"fila {i}: debe ser texto u objeto.")
                col = f.get("color", "white")
                if col not in ACENTOS:
                    raise Rechazo(f"fila {i}: color '{col}' no existe. "
                                  f"Validos: {sorted(ACENTOS)}.")
                limpias.append({
                    "txt": self._texto(f.get("txt"), f"filas[{i}].txt", ancho).upper(),
                    "color": col,
                    **({"badge": self._texto(f.get("badge"), "badge", 3).upper()}
                       if f.get("badge") else {}),
                })
            acento = a.get("acento", "cyan")
            if acento not in ACENTOS:
                raise Rechazo(f"acento '{acento}' no existe. Validos: {sorted(ACENTOS)}.")
            return {"id": ident,
                    "titulo": self._texto(a.get("titulo"), "titulo", 10).upper(),
                    "filas": limpias, "acento": acento,
                    "orden": self._entero(a.get("orden"), "orden", 0, 99, 99),
                    "ttl": self._entero(a.get("ttl"), "ttl", 0, 86400, 0),
                    "__dry_run__": seco}

        if cmd == "borrar":
            return {"id": self._texto(a.get("id"), "id", 15), "__dry_run__": seco}

        if cmd == "notifica":
            niv = a.get("nivel", "info")
            if niv not in NIVELES:
                raise Rechazo(f"nivel '{niv}' no existe. Validos: {sorted(NIVELES)}.")
            return {"txt": self._texto(a.get("txt"), "txt", ancho * 2).upper(),
                    "nivel": niv, "beep": bool(a.get("beep")), "__dry_run__": seco}

        if cmd == "hablar":
            t = self._texto(a.get("texto"), "texto", 300)
            if not t.strip():
                raise Rechazo("No se dice nada vacio en voz alta.")
            return {"texto": t, "__dry_run__": seco}

        if cmd == "consulta":
            return {"qid": self._texto(a.get("qid"), "qid", 12),
                    "__dry_run__": seco}

        if cmd in ("pregunta", "pregunta_async"):
            txt = self._texto(a.get("txt"), "txt", ancho * 2).upper()
            if not txt.strip():
                raise Rechazo("Una pregunta sin texto no es una pregunta.")
            ops = a.get("opciones") or ["SI", "NO"]
            if not isinstance(ops, list) or not 1 <= len(ops) <= 3:
                raise Rechazo("'opciones' debe ser una lista de 1 a 3 etiquetas.")
            return {"txt": txt,
                    "opciones": [self._texto(o, "opcion", 10).upper() for o in ops],
                    "timeout": self._entero(a.get("timeout"), "timeout", 5, 300, 30),
                    "__dry_run__": seco}

        if cmd == "configurar":
            out = {"__dry_run__": seco}
            if a.get("brillo") is not None:
                out["brillo"] = self._entero(a.get("brillo"), "brillo", 0, 100, 80)
            if a.get("volumen") is not None:
                out["volumen"] = self._entero(a.get("volumen"), "volumen", 0, 100, 70)
            if a.get("tema_hud") is not None:
                tema = a.get("tema_hud")
                if tema not in ACENTOS:
                    raise Rechazo(f"tema_hud '{tema}' no existe. Validos: {sorted(ACENTOS)}.")
                out["tema_hud"] = tema
            if a.get("efectos") is not None:
                out["efectos"] = bool(a.get("efectos"))
            return out

        if cmd == "pantallas":
            act = a.get("activas")
            if not isinstance(act, list) or not act:
                raise Rechazo("'activas' debe ser una lista no vacia de pantallas.")
            if len(act) > 10:
                raise Rechazo(f"Llegaron {len(act)} pantallas y solo hay 10 fijas.")
            orden = a.get("orden")
            if orden is not None and not isinstance(orden, list):
                raise Rechazo("'orden' debe ser una lista.")
            # Los nombres/indices se validan en Canal.pantallas contra la
            # lista real del firmware: aqui solo se acota forma y tamano.
            return {"activas": act, "orden": orden, "__dry_run__": seco}

        if cmd == "reiniciar":
            return {"__dry_run__": seco}

        return {"__dry_run__": seco}

    # ---------------- integridad de las aprobaciones ----------------
    def sella(self, qid: str, txt: str, opciones: list) -> str:
        """Firma una pregunta al emitirla.

        Sin esto, la respuesta que vuelve del dispositivo no se puede atar a la
        pregunta que se hizo: un agente podria emitir una pregunta inocua y
        cobrar la aprobacion para otra cosa. El sello ata qid, texto y opciones.
        """
        msg = f"{qid}|{txt}|{'|'.join(opciones)}".encode()
        firma = hmac.new(_SECRETO, msg, hashlib.sha256).hexdigest()[:16]
        self._preguntas[qid] = firma
        return firma

    def verifica(self, qid: str, txt: str, opciones: list) -> bool:
        esperado = self._preguntas.get(qid)
        if not esperado:
            return False
        msg = f"{qid}|{txt}|{'|'.join(opciones)}".encode()
        actual = hmac.new(_SECRETO, msg, hashlib.sha256).hexdigest()[:16]
        return hmac.compare_digest(esperado, actual)

    def cierra(self, qid: str):
        self._preguntas.pop(qid, None)


GUARDIA = Guardia()
