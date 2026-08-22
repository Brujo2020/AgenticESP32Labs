"""
Agente con bucle de herramientas.

El modelo puede pedir herramientas MCP; se ejecutan, se le devuelven los
resultados y se le vuelve a preguntar. Se repite hasta que responde con
texto o hasta agotar el limite de vueltas.

Esto es lo que faltaba: antes los MCP se conectaban pero nunca se invocaban.
"""
import json, logging, time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import Config
from .mcp_pool import MCPPool

log = logging.getLogger("agente")

MAX_VUELTAS = 5          # tope para que un modelo terco no entre en bucle

SISTEMA = """Eres el asistente de voz de Mario, embebido en un dispositivo ESP32
con pantalla circular. Respondes en espanol, en frases cortas y naturales,
porque tu respuesta se lee en voz alta.

Tienes herramientas para consultar el clima, el estado del Mac de Mario y otras
fuentes. Usalas cuando la pregunta lo pida en lugar de suponer datos.
Si no sabes algo y no hay herramienta para averiguarlo, dilo claramente.
No uses emojis ni markdown: se van a pronunciar."""


AJUSTES_PATH = Path(__file__).resolve().parent.parent / "ajustes.yaml"


def _lee_ajustes(datos: dict) -> dict:
    """Los ajustes que edita el panel web.

    Viven en servidor/ajustes.yaml. Estuvieron dentro de config.yaml, pero eso
    rompia el despliegue: config.yaml esta versionado, asi que tocar un ajuste
    desde el panel dejaba cambios locales y el siguiente 'git pull' abortaba.

    Se sigue mirando config.yaml como respaldo para no perder los ajustes de
    una instalacion que todavia no haya migrado.
    """
    if AJUSTES_PATH.exists():
        try:
            return yaml.safe_load(AJUSTES_PATH.read_text()) or {}
        except Exception as e:
            log.warning("ajustes.yaml ilegible (%s); se usan los de config.yaml", e)
    return (datos or {}).get("ajustes") or {}


def _prompt_desde_ajustes(datos: dict) -> tuple[str, list | None]:
    """Traduce los ajustes del panel web a lo que el agente realmente usa:
    prompt de sistema y filtro de herramientas.

    Sin esto, la ciudad del clima y los agentes del panel eran solo texto
    guardado en un YAML que nadie leia.

    Devuelve (prompt_sistema, mcp_permitidos_o_None).
    """
    aj = _lee_ajustes(datos)
    partes = [SISTEMA]

    ciudad = ((aj.get("clima") or {}).get("ciudad") or "").strip()
    if ciudad:
        partes.append(
            f"\nLa ubicacion por defecto de Mario es {ciudad}. Cuando pregunte "
            f"por el clima sin decir donde, usa esa ciudad: resuelve primero sus "
            f"coordenadas con clima_ubicacion y luego consulta clima_actual. No "
            f"le preguntes la ciudad si no ha cambiado de sitio.")

    nombre = ((aj.get("apariencia") or {}).get("nombre_asistente") or "").strip()
    if nombre and nombre.lower() not in ("asistente esp32", ""):
        partes.append(f"\nTe llamas {nombre}. Si te preguntan tu nombre, ese es.")

    # Perfil de agente activo: el primero con nombre. Sus instrucciones se
    # anaden al sistema y su lista de MCP acota las herramientas visibles.
    permitidos = None
    for a in (aj.get("agentes") or []):
        if not isinstance(a, dict) or not (a.get("nombre") or "").strip():
            continue
        instr = (a.get("instrucciones") or "").strip()
        if instr:
            partes.append(f"\nInstrucciones adicionales ({a['nombre']}):\n{instr}")
        if a.get("mcp"):
            permitidos = [str(x).strip() for x in a["mcp"] if str(x).strip()]
        break

    return "\n".join(partes), permitidos


@dataclass
class Agente:
    config: Config = None
    mcp_pool: MCPPool = None
    cadena_llm: object = None                 # proveedores.Cadena
    historial: list = field(default_factory=list)
    modelo_actual: str = "cadena"

    def __post_init__(self):
        self.config = self.config or Config()

    async def initialize(self):
        if self.mcp_pool:
            await self.mcp_pool.connect_all()
        if self.cadena_llm is None:
            from proveedores import cadenas_desde_config
            self.cadena_llm = cadenas_desde_config(self.config.data)["llm"]

    # ------------------------------------------------------------
    async def chat(self, mensaje_usuario: str) -> str:
        self.historial.append({"role": "user", "content": mensaje_usuario})
        # El prompt se recalcula en cada turno a proposito: asi un cambio
        # guardado desde el panel web (ciudad del clima, nombre, perfil de
        # agente) entra en el siguiente mensaje sin reiniciar el bridge.
        if self.config:
            self.config.recarga_si_cambio()
        sistema, permitidos = _prompt_desde_ajustes(
            self.config.data if self.config else {})
        mensajes = [{"role": "system", "content": sistema}] + self.historial[-12:]
        herramientas = self.mcp_pool.esquemas(permitidos) if self.mcp_pool else []

        for vuelta in range(MAX_VUELTAS):
            t0 = time.time()
            msg = await self.cadena_llm.chat_completo(mensajes, herramientas or None)
            log.info("vuelta %d en %.1fs", vuelta + 1, time.time() - t0)

            llamadas = msg.get("tool_calls") or []
            if not llamadas:
                texto = (msg.get("content") or "").strip()
                self.historial.append({"role": "assistant", "content": texto})
                return texto

            # El modelo pidio herramientas: se ejecutan y se le devuelven.
            # Si la cadena degrada a otro proveedor a mitad de conversacion,
            # ese historial (con este mismo msg) se reenvia tal cual. Un
            # tool_call sin argumentos puede llegar con arguments=None desde
            # el proveedor original; algunos backends (el servidor OpenAI-
            # compatible local, por ejemplo) exigen que sea un JSON string
            # valido y rechazan null con 422. Se normaliza aqui, una vez,
            # antes de que quede fijado en el historial.
            for lc in llamadas:
                fn_info = lc.get("function") or {}
                if not fn_info.get("arguments"):
                    fn_info["arguments"] = "{}"
            mensajes.append(msg)
            for lc in llamadas:
                fn = lc["function"]["name"]
                try:
                    args = json.loads(lc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                log.info("herramienta %s(%s)", fn, args)
                resultado = await self.mcp_pool.invoca(fn, args)
                mensajes.append({
                    "role": "tool",
                    "tool_call_id": lc.get("id", fn),
                    "name": fn,
                    "content": resultado[:4000],
                })

        aviso = "No consegui cerrar la consulta tras varios intentos."
        self.historial.append({"role": "assistant", "content": aviso})
        return aviso

    # ------------------------------------------------------------
    def limpiar(self):
        self.historial.clear()

    def herramientas_disponibles(self) -> list[str]:
        return list(self.mcp_pool.herramientas) if self.mcp_pool else []

    def set_model(self, nombre: str):
        """Reordena la cadena para poner delante el proveedor pedido."""
        m = [p for p in self.cadena_llm.miembros if p.nombre == nombre]
        if not m:
            raise ValueError(
                f"'{nombre}' no esta activo. Activos: "
                f"{[p.nombre for p in self.cadena_llm.miembros]}")
        self.cadena_llm.miembros.remove(m[0])
        self.cadena_llm.miembros.insert(0, m[0])
        self.modelo_actual = nombre

    def get_available_models(self) -> list[dict]:
        return [{"name": p.nombre, "description": getattr(p, "model", "")}
                for p in self.cadena_llm.miembros]
