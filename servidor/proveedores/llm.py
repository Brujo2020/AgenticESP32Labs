"""
LLM. Una sola clase cubre casi todo el mercado porque Groq, NVIDIA NIM,
Cloudflare Workers AI, Azure OpenAI, Together, Fireworks, vLLM y MLX
exponen la misma API que OpenAI: solo cambia base_url y el modelo.

Bedrock es la excepcion: no habla el esquema de OpenAI (usa su propia
Converse API), asi que va en una clase aparte (ver LLMBedrock) que traduce
mensajes/herramientas en ambas direcciones para que agente.py no tenga que
saber que el proveedor activo cambio.
"""
import asyncio, os, json, httpx
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

    async def _llama(self, mensajes, herramientas):
        cuerpo = {
            "model": self.model,
            "messages": mensajes,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if herramientas:
            cuerpo["tools"] = herramientas
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            return await c.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=cuerpo,
            )

    async def chat_completo(self, mensajes, herramientas=None) -> dict:
        """Devuelve el mensaje entero para poder leer tool_calls."""
        try:
            r = await self._llama(mensajes, herramientas)
            if r.status_code == 400 and herramientas and "tool_use_failed" in r.text:
                # El modelo a veces alucina la sintaxis del tool-call (mete
                # el JSON de argumentos dentro del NOMBRE de la funcion, p.ej.
                # 'hablar{"texto": "De nada"}' en vez de nombre+argumentos
                # separados). Groq lo rechaza con 400 porque no matchea
                # ninguna tool declarada -- no es un bug de nuestro schema,
                # es el modelo generando mal. Sin este reintento, un solo
                # tropiezo del modelo tumbaba TODA la respuesta (y en cascada
                # probaba nvidia/mlx, que tambien pueden fallar, dejando al
                # usuario sin nada). Reintentar una vez SIN herramientas letra
                # que al menos conteste en texto, aunque esa vuelta pierda la
                # posibilidad de llamar una tool.
                r = await self._llama(mensajes, None)
            if r.status_code >= 400:
                # El texto de la respuesta trae la razon real (p.ej. que
                # herramienta/schema rechazo la API) - sin esto solo se ve
                # "400 Bad Request" y hay que adivinar la causa.
                raise ErrorProveedor(f"{self.nombre}: {r.status_code} {r.text}")
            return r.json()["choices"][0]["message"]
        except ErrorProveedor:
            raise
        except Exception as e:
            # Algunas excepciones (p.ej. httpx.ReadTimeout) tienen str(e)
            # vacio - sin el nombre del tipo, el log solo dice "nvidia: "
            # y no hay forma de saber si fue timeout, DNS, TLS, etc.
            detalle = str(e) or type(e).__name__
            raise ErrorProveedor(f"{self.nombre}: {detalle}") from e


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


class LLMBedrock(ProveedorLLM):
    """AWS Bedrock (Converse API) -- pensado para Claude 3.5 Haiku.

    Bedrock no usa el esquema {"role","content"} plano de OpenAI para
    herramientas: los tool_calls y sus resultados van como bloques dentro
    de "content" ({"toolUse": ...} / {"toolResult": ...}), y el "system"
    es una lista aparte, no un mensaje mas. Toda esa traduccion vive aqui
    para que agente.py siga escribiendo mensajes al estilo OpenAI sin
    enterarse de que el proveedor activo es Bedrock.

    Las credenciales NO se leen de config.yaml: boto3 las toma solo del
    entorno (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION),
    igual que hace con cualquier SDK de AWS. Lightsail no soporta roles de
    IAM adjuntos a la instancia (eso es una capacidad especifica de EC2),
    asi que en este servidor la unica via es un usuario IAM con su access
    key puesta en .env.
    """

    def __init__(self, nombre: str, cfg: dict):
        self.nombre = nombre
        self.model = cfg.get("model", "us.anthropic.claude-3-5-haiku-20241022-v1:0")
        self.region = cfg.get("region") or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        self.max_tokens = cfg.get("max_tokens", 2048)
        self.temperature = cfg.get("temperature", 0.7)
        self._cliente = None

    def disponible(self) -> bool:
        try:
            import boto3  # noqa: F401
        except ImportError:
            return False
        return bool(os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"))

    def _boto(self):
        if self._cliente is None:
            import boto3
            self._cliente = boto3.client("bedrock-runtime", region_name=self.region)
        return self._cliente

    @staticmethod
    def _a_mensajes(mensajes):
        """OpenAI-style -> (system: list[dict], messages: list[dict]) de Bedrock."""
        sistema, salida = [], []
        for m in mensajes:
            rol = m["role"]
            if rol == "system":
                if m.get("content"):
                    sistema.append({"text": m["content"]})
            elif rol == "tool":
                # Un resultado de tool en Bedrock viaja como mensaje "user"
                # con un bloque toolResult (no existe el rol "tool" aparte).
                salida.append({"role": "user", "content": [{
                    "toolResult": {
                        "toolUseId": m.get("tool_call_id") or m.get("name", ""),
                        "content": [{"text": m.get("content") or ""}],
                    }
                }]})
            elif rol == "assistant":
                bloques = []
                if m.get("content"):
                    bloques.append({"text": m["content"]})
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function", {})
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    bloques.append({"toolUse": {
                        "toolUseId": tc.get("id", fn.get("name", "")),
                        "name": fn.get("name", ""),
                        "input": args,
                    }})
                salida.append({"role": "assistant", "content": bloques or [{"text": ""}]})
            else:  # user
                salida.append({"role": "user", "content": [{"text": m.get("content") or ""}]})
        return sistema, salida

    @staticmethod
    def _a_herramientas(herramientas):
        if not herramientas:
            return None
        specs = []
        for h in herramientas:
            fn = h["function"]
            specs.append({"toolSpec": {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "inputSchema": {"json": fn.get("parameters") or {"type": "object", "properties": {}}},
            }})
        return {"tools": specs}

    @staticmethod
    def _de_respuesta(resp):
        """Respuesta de Converse -> dict estilo OpenAI (content + tool_calls)."""
        msg = resp.get("output", {}).get("message", {})
        texto, tool_calls = [], []
        for bloque in msg.get("content", []):
            if "text" in bloque:
                texto.append(bloque["text"])
            elif "toolUse" in bloque:
                tu = bloque["toolUse"]
                tool_calls.append({
                    "id": tu.get("toolUseId", tu.get("name", "")),
                    "type": "function",
                    "function": {
                        "name": tu.get("name", ""),
                        "arguments": json.dumps(tu.get("input") or {}),
                    },
                })
        salida = {"role": "assistant", "content": "".join(texto)}
        if tool_calls:
            salida["tool_calls"] = tool_calls
        return salida

    def _invoca_sync(self, sistema, mensajes, herramientas):
        cliente = self._boto()
        kwargs = {
            "modelId": self.model,
            "messages": mensajes,
            "inferenceConfig": {"maxTokens": self.max_tokens, "temperature": self.temperature},
        }
        if sistema:
            kwargs["system"] = sistema
        if herramientas:
            kwargs["toolConfig"] = herramientas
        return cliente.converse(**kwargs)

    async def chat_completo(self, mensajes, herramientas=None) -> dict:
        sistema, msgs_bedrock = self._a_mensajes(mensajes)
        tools_bedrock = self._a_herramientas(herramientas)
        try:
            # boto3 es sincrono (no hay cliente async oficial de Bedrock) --
            # to_thread para no bloquear el loop de asyncio mientras espera
            # la respuesta del modelo.
            resp = await asyncio.to_thread(self._invoca_sync, sistema, msgs_bedrock, tools_bedrock)
            return self._de_respuesta(resp)
        except Exception as e:
            detalle = str(e) or type(e).__name__
            raise ErrorProveedor(f"{self.nombre}: {detalle}") from e

    async def chat(self, mensajes, herramientas=None) -> str:
        msg = await self.chat_completo(mensajes, herramientas)
        return msg.get("content") or ""


REGISTRO_LLM = {
    "openai-compatible": LLMCompatibleOpenAI,   # mlx, groq, nvidia, cloudflare, azure, vllm...
    "anthropic": LLMAnthropic,
    "bedrock": LLMBedrock,
}
