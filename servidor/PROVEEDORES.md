# Capa de proveedores — cómo cambiar de hyperscaler

## La idea

Tres capacidades (**LLM**, **STT**, **TTS**), una interfaz por capacidad, y una
**lista ordenada** por cada una en `config.yaml`. Se usa el primero disponible;
si falla en caliente, la cadena degrada al siguiente automáticamente.

Cambiar de nube = reordenar una lista. No se toca el firmware ni el agente.

```yaml
proveedores:
  llm: [groq, nvidia, mlx]      # primero Groq; si cae, NVIDIA; si no hay red, local
  stt: [groq, mlx-whisper]
  tts: [macos]
```

## Por qué se empieza por Groq

| | Groq | NVIDIA NIM | Cloudflare | Azure / GCP / AWS | MLX local |
|---|---|---|---|---|---|
| Coste | gratis | gratis | gratis con límites | de pago | gratis |
| Alta | minutos | minutos | minutos | días (cuenta, cuotas) | ya está |
| Velocidad | la mayor | alta | alta | media | según Mac |
| Tool calling (MCP) | sí | sí | parcial | sí | según modelo |
| STT incluido | sí (Whisper v3) | no | no | sí | sí (local) |

Groq cubre LLM **y** STT con una sola clave, es gratis y tiene *tool calling*
fiable — que es lo que hace que los MCP sirvan de algo. Un modelo pequeño local
suele no soportarlo, y sin eso el agente no puede invocar herramientas.

## Poner en marcha (2 minutos)

```bash
export GROQ_API_KEY=gsk_...        # console.groq.com
pip install httpx websockets pyyaml
python3 websocket_bridge.py
```

Al arrancar, el log dice qué cadena quedó activa:

```
llm -> groq > nvidia > mlx
stt -> groq > mlx-whisper
tts -> macos
```

## Demos según el escenario

- **Edge / privada (sin salir de la máquina):** `llm: [mlx]`, `stt: [mlx-whisper]`, `tts: [macos]`
- **Hyperscaler:** `llm: [azure]` o `[gcp]` o `[aws]`
- **Edge global:** `llm: [cloudflare]`
- **Resistencia a fallos:** deja varios y desenchufa el primero en vivo —
  la cadena degrada sola y la demo no se cae.

## Añadir un proveedor nuevo

1. Clase que herede de `ProveedorLLM` / `ProveedorSTT` / `ProveedorTTS`
   en `proveedores/llm.py`, `stt.py` o `tts.py`.
2. Regístrala en el `REGISTRO_*` de ese archivo.
3. Entrada en el `catalogo` de `config.yaml`.

Si el proveedor habla el esquema de OpenAI (la mayoría: Groq, NVIDIA, Cloudflare,
Azure, Together, Fireworks, vLLM, Ollama, LM Studio) **no hace falta escribir código**:
basta una entrada en el catálogo con `backend: openai-compatible` y su `base_url`.

## Añadir herramientas MCP

En `mcp_servers` del `config.yaml` y luego actívalas en `tools_to_enable`.
Los oficiales se descargan solos con `npx`, sin instalar nada:

```yaml
  ficheros:
    command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/ruta"]
  memoria:
    command: ["npx", "-y", "@modelcontextprotocol/server-memory"]
```
