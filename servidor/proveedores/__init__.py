"""
Fabrica y cadena de respaldo.

La idea: cada capacidad (llm, stt, tts) se declara en config.yaml como una
LISTA ordenada. Se usa el primero que este disponible; si falla en caliente,
se pasa al siguiente. Cambiar de hyperscaler es reordenar esa lista.
"""
import logging
from .base import ErrorProveedor, Proveedor, ProveedorLLM, ProveedorSTT, ProveedorTTS
from .llm import REGISTRO_LLM
from .stt import REGISTRO_STT
from .tts import REGISTRO_TTS

log = logging.getLogger("proveedores")

REGISTROS = {"llm": REGISTRO_LLM, "stt": REGISTRO_STT, "tts": REGISTRO_TTS}


def construir(capacidad: str, nombre: str, cfg: dict) -> Proveedor:
    backend = cfg.get("backend")
    registro = REGISTROS[capacidad]
    if backend not in registro:
        raise ValueError(
            f"backend '{backend}' desconocido para {capacidad}. "
            f"Disponibles: {sorted(registro)}")
    return registro[backend](nombre, cfg)


class Cadena:
    """Lista ordenada de proveedores de una misma capacidad, con respaldo."""

    def __init__(self, capacidad: str, orden: list[str], catalogo: dict):
        self.capacidad = capacidad
        self.miembros: list[Proveedor] = []
        self.recargar(orden, catalogo, _silencioso=True)

    def recargar(self, orden: list[str], catalogo: dict, _silencioso: bool = False):
        """Reconstruye 'miembros' con un orden/catalogo nuevos, EN EL MISMO
        objeto (self.miembros se reemplaza, pero el objeto Cadena en si nunca
        cambia de identidad).

        Eso es lo que permite el hot-reload de Fase 0
        (001-panel-administracion-mcp/tasks.md): tanto 'agente.cadena_llm'
        como el 'cadenas[...]' de websocket_bridge.py guardan una referencia
        a este MISMO objeto Cadena, asi que reordenar proveedores desde el
        panel web se refleja aqui sin que nadie tenga que reasignar esa
        referencia en otro proceso/hilo.
        """
        nuevos: list[Proveedor] = []
        for nombre in orden:
            if nombre not in catalogo:
                log.warning("%s: '%s' no esta en el catalogo, se omite", self.capacidad, nombre)
                continue
            try:
                p = construir(self.capacidad, nombre, catalogo[nombre])
            except Exception as e:
                log.warning("%s: no se pudo construir '%s': %s", self.capacidad, nombre, e)
                continue
            if not p.disponible():
                log.info("%s: '%s' sin credenciales o dependencias, se omite",
                         self.capacidad, nombre)
                continue
            nuevos.append(p)
        self.miembros = nuevos
        if self.miembros:
            log.info("%s -> %s", self.capacidad, " > ".join(m.nombre for m in self.miembros))
        elif not _silencioso:
            log.error("%s: ningun proveedor disponible tras recargar", self.capacidad)

    @property
    def activo(self):
        return self.miembros[0] if self.miembros else None

    def _degradar(self, fallido):
        """Manda al final al que fallo, para no reintentarlo de inmediato."""
        if fallido in self.miembros and len(self.miembros) > 1:
            self.miembros.remove(fallido)
            self.miembros.append(fallido)

    async def chat(self, mensajes, herramientas=None) -> str:
        ultimo = None
        for p in list(self.miembros):
            try:
                return await p.chat(mensajes, herramientas)
            except ErrorProveedor as e:
                log.warning("llm '%s' fallo, probando el siguiente: %s", p.nombre, e)
                ultimo = e
                self._degradar(p)
        raise ultimo or RuntimeError("sin proveedor de llm")

    async def chat_completo(self, mensajes, herramientas=None) -> dict:
        """Como chat() pero devuelve el mensaje entero, con tool_calls si los hay."""
        ultimo = None
        for p in list(self.miembros):
            try:
                return await p.chat_completo(mensajes, herramientas)
            except ErrorProveedor as e:
                log.warning("llm '%s' fallo: %s", p.nombre, e)
                ultimo = e
                self._degradar(p)
        raise ultimo or RuntimeError("sin proveedor de llm")

    def transcribir(self, wav_path, idioma="es") -> str:
        ultimo = None
        for p in list(self.miembros):
            try:
                return p.transcribir(wav_path, idioma)
            except ErrorProveedor as e:
                log.warning("stt '%s' fallo: %s", p.nombre, e)
                ultimo = e
                self._degradar(p)
        raise ultimo or RuntimeError("sin proveedor de stt")

    def sintetizar(self, texto, sample_rate=16000) -> bytes:
        ultimo = None
        for p in list(self.miembros):
            try:
                return p.sintetizar(texto, sample_rate)
            except ErrorProveedor as e:
                log.warning("tts '%s' fallo: %s", p.nombre, e)
                ultimo = e
                self._degradar(p)
        raise ultimo or RuntimeError("sin proveedor de tts")


def cadenas_desde_config(cfg: dict) -> dict[str, Cadena]:
    """Lee la seccion 'proveedores' de config.yaml y devuelve las tres cadenas."""
    sec = cfg.get("proveedores", {})
    catalogo = cfg.get("catalogo", {})
    return {
        cap: Cadena(cap, sec.get(cap, []), catalogo.get(cap, {}))
        for cap in ("llm", "stt", "tts")
    }


def recargar_cadenas(cadenas: dict[str, Cadena], cfg: dict):
    """Hot-reload de Fase 0: recarga las TRES cadenas ya existentes en vez de
    crear objetos Cadena nuevos, para que quien ya tenga una referencia
    (agente.cadena_llm, el 'cadenas' modulo-nivel de websocket_bridge.py) vea
    el cambio sin que nadie tenga que reasignarla. Se llama cuando
    Config.recarga_si_cambio() detecta que config.yaml cambio en disco.
    """
    sec = cfg.get("proveedores", {})
    catalogo = cfg.get("catalogo", {})
    for cap in ("llm", "stt", "tts"):
        if cap in cadenas:
            cadenas[cap].recargar(sec.get(cap, []), catalogo.get(cap, {}))
