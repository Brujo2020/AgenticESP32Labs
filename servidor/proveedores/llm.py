"""
LLM. Una sola clase cubre casi todo el mercado porque Groq, NVIDIA NIM,
Cloudflare Workers AI, Azure OpenAI, Together, Fireworks, vLLM y MLX
exponen la misma API que OpenAI: solo cambia base_url y el modelo.
"""
import os, json, httpx
from .base import ProveedorLLM, ErrorProveedor


def _expande(valor: str) -> str:
    """Permite escribir ${VARIABLE} en config.yaml."""
    if isinstance(valor, str) and valor.startswith("${") and valor.endswith("}"):
        return os.getenv(valor[2:-1], "")
    return valor


class LLMCompatibleOpenAI(ProveedorLLM):
    def __init__(self, nombre: str, cfg: dict):
        self.nombre = nombre
        self.base_url = cfg["base_url"].rstrip("/")
        self.model = cfg["model"]
        self.api_key = _expande(cfg.get("api_key", "")) or "no-necesaria"
        self.temperature = cfg.get("temperature", 0.7)
        self.max_tokens = cfg.get("max_tokens", 2048)
        self.timeout = cfg.get("timeout", 60)

    def disponible(self) -> bool:
        # Los locales no necesitan clave; los remotos si
        if "localhost" in self.base_url or "127.0.0.1" in self.base_url:
            return True
        return bool(self.api_key and self.api_key != "no-necesaria")

    async def chat(self, mensajes, herramientas=None) -> str:
        msg = await self.chat_completo(mensajes, herramientas)
        return msg.get("content") or ""

    async def chat_completo(self, mensajes, herramientas=None) -> dict:
        """Devuelve el mensaje entero para poder leer tool_calls."""
        cuerpo = {
            "model": self.model,
            "messages": mensajes,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if herramientas:
            cuerpo["tools"] = herramientas
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=cuerpo,
                )
                r.raise_for_status()
                return r.json()["choices"][0]["message"]
        except Exception as e:
            raise ErrorProveedor(f"{self.nombre}: {e}") from e


class LLMAnthropic(ProveedorLLM):
    """Claude no usa el esquema de OpenAI, por eso va aparte."""

    def __init__(self, nombre: str, cfg: dict):
        self.nombre = nombre
        self.model = cfg.get("model", "claude-sonnet-4-5")
        self.api_key = _expande(cfg.get("api_key", "")) or os.getenv("ANTHROPIC_API_KEY", "")
        self.max_tokens = cfg.get("max_tokens", 2048)

    def disponible(self) -> bool:
        return bool(self.api_key)

    async def chat(self, mensajes, herramientas=None) -> str:
        sistema = "".join(m["content"] for m in mensajes if m["role"] == "system")
        turnos = [m for m in mensajes if m["role"] != "system"]
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": self.api_key,
                             "anthropic-version": "2023-06-01"},
                    json={"model": self.model, "max_tokens": self.max_tokens,
                          "system": sistema, "messages": turnos},
                )
                r.raise_for_status()
                return "".join(b.get("text", "") for b in r.json()["content"])
        except Exception as e:
            raise ErrorProveedor(f"{self.nombre}: {e}") from e

    async def chat_completo(self, mensajes, herramientas=None) -> dict:
        return {"role": "assistant", "content": await self.chat(mensajes, herramientas)}


REGISTRO_LLM = {
    "openai-compatible": LLMCompatibleOpenAI,   # mlx, groq, nvidia, cloudflare, azure, vllm...
    "anthropic": LLMAnthropic,
}
