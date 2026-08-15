"""
Agente con bucle de herramientas.

El modelo puede pedir herramientas MCP; se ejecutan, se le devuelven los
resultados y se le vuelve a preguntar. Se repite hasta que responde con
texto o hasta agotar el limite de vueltas.

Esto es lo que faltaba: antes los MCP se conectaban pero nunca se invocaban.
"""
import json, logging, time
from dataclasses import dataclass, field

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
        mensajes = [{"role": "system", "content": SISTEMA}] + self.historial[-12:]
        herramientas = self.mcp_pool.esquemas() if self.mcp_pool else []

        for vuelta in range(MAX_VUELTAS):
            t0 = time.time()
            msg = await self.cadena_llm.chat_completo(mensajes, herramientas or None)
            log.info("vuelta %d en %.1fs", vuelta + 1, time.time() - t0)

            llamadas = msg.get("tool_calls") or []
            if not llamadas:
                texto = (msg.get("content") or "").strip()
                self.historial.append({"role": "assistant", "content": texto})
                return texto

            # El modelo pidio herramientas: se ejecutan y se le devuelven
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
