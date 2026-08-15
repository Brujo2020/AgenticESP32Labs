"""
Canal hacia el ESP32 conectado.

Singleton porque hay un solo dispositivo. Lo usan tanto el puente WebSocket
(que lo alimenta) como el MCP 'dispositivo' (que lo consume desde otro proceso
via stdio -> ver mcps/dispositivo.py, que habla por HTTP local con este canal).

Concentra dos responsabilidades que no conviene repartir:
  - saber si hay dispositivo y cuales son sus limites reales (handshake)
  - resolver las preguntas pendientes, que son peticiones bloqueantes
"""
import asyncio, json, logging, time
from dataclasses import dataclass, field

log = logging.getLogger("canal")

# Limites por defecto. El handshake del firmware los sobreescribe: son el
# tamano de sus buffers estaticos, no una preferencia.
LIMITES = {"vistas_max": 8, "filas_max": 6, "ancho": 26}

ACENTOS = {"cyan", "magenta", "lime", "amber", "ice", "blood", "grey", "white"}
NIVELES = {"info", "ok", "warn", "error"}


@dataclass
class Canal:
    ws: object = None
    fw: str = ""
    limites: dict = field(default_factory=lambda: dict(LIMITES))
    vistas: dict = field(default_factory=dict)
    _pendientes: dict = field(default_factory=dict)   # qid -> Future
    _qid: int = 0
    estado: dict = field(default_factory=dict)

    # ---------------- conexion ----------------
    @property
    def vivo(self) -> bool:
        return self.ws is not None

    def conecta(self, ws):
        self.ws = ws
        log.info("dispositivo conectado")

    def desconecta(self):
        self.ws = None
        # Nadie va a contestar: liberar a quien espere, no dejarlo colgado
        for fut in self._pendientes.values():
            if not fut.done():
                fut.set_result(-1)
        self._pendientes.clear()
        self.vistas.clear()
        log.info("dispositivo desconectado")

    def saluda(self, data: dict):
        self.fw = data.get("fw", "?")
        for k in LIMITES:
            if isinstance(data.get(k), int) and data[k] > 0:
                self.limites[k] = data[k]
        log.info("handshake fw=%s limites=%s", self.fw, self.limites)

    async def _envia(self, obj: dict):
        if not self.ws:
            raise RuntimeError("no hay dispositivo conectado")
        await self.ws.send(json.dumps(obj, ensure_ascii=False))

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

    async def mostrar(self, id: str, titulo: str, filas: list,
                      acento: str = "cyan", orden: int = 99, ttl: int = 0) -> str:
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
        opciones = [str(o).upper()[:10] for o in (opciones or ["SI", "NO"])][:3]
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

    def resuelve(self, qid: str, opcion: int):
        fut = self._pendientes.get(qid)
        if fut and not fut.done():
            fut.set_result(int(opcion))

    # ---------------- otros ----------------
    async def notifica(self, txt: str, nivel: str = "info", beep: bool = False) -> str:
        if nivel not in NIVELES:
            nivel = "info"
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
            "firmware": self.fw or None,
            "vistas_activas": sorted(self.vistas),
            "limites": self.limites,
            **self.estado,
        }


CANAL = Canal()
