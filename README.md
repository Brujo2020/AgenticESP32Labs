# AgenticESP32Labs

Asistente de voz sobre **ESP32-S3** con pantalla circular, agente con
herramientas MCP y proveedores de IA intercambiables.

El dispositivo escucha, el Mac piensa, y la respuesta vuelve por el parlante
y por la pantalla.

## Qué hace

- **Voz**: mantener pulsado el botón central envía el micrófono al servidor;
  se transcribe, el agente responde y el audio vuelve al parlante.
- **Herramientas MCP**: el agente consulta el clima, el estado del Mac,
  ficheros, GitHub, búsqueda web y lo que actives del catálogo.
- **Diez pantallas** táctiles: núcleo, reloj, clima, voz, conversación,
  noticias de IA, telemetría del Mac, Unity/Blender, ajustes y diagnóstico.
- **Ajustes persistentes**: brillo (PWM), volumen, tema y efectos, guardados en NVS.

## Hardware

Spotpear **ESP32-S3-1.28-BOX** (N16R8): pantalla GC9A01A 240×240 redonda,
táctil CST816, códec de audio ES8311 con micrófono y parlante.

Pinout completo y trampas documentadas en `components/board/include/board_pins.h`.

## Estructura

```
components/
  board/     pinout — única fuente de verdad
  display/   GC9A01A, framebuffer, primitivas de dibujo, fuente 5x7
  touch/     CST816 sobre la API i2c_master
  audio/     ES8311 (mic + parlante) e I2S full duplex
  net/       WiFi, hora por SNTP, clima y descubrimiento mDNS
  voice/     cliente WebSocket contra el servidor
  ajustes/   preferencias en NVS
  hud/       pantallas y botones táctiles
main/        orquestación

servidor/    el agente, en el Mac
  proveedores/  LLM, STT y TTS intercambiables con cadena de respaldo
  nucleo/       agente con bucle de herramientas y pool de MCP
  mcps/         herramientas propias (clima, telemetría del Mac, sistema)
  mcps_cli.py   gestor interactivo de MCP
```

## Puesta en marcha

**Firmware** — ESP-IDF v6.0, target esp32s3.

Primero la configuración local (una sola vez). Nada de esto se versiona:
vive en `sdkconfig`, que git ignora.

```bash
idf.py menuconfig     # AgenticESP32Labs - Configuracion del HUD
```

| Menú | Qué se define |
|------|---------------|
| Red WiFi | SSID, password, reintentos |
| Servidor agentico | host y puerto de fallback, hostname y timeout mDNS |
| Localización | zona horaria POSIX, latitud y longitud para el clima |

Luego, el ciclo normal:

```bash
./run.sh          # o en VSCode: Cmd+Shift+B
```

Compila, espera a que aparezca el puerto, flashea y captura el arranque
en `boot.log`.

**Servidor** — en el Mac:

```bash
cd servidor
pip install -r requirements.txt
export GROQ_API_KEY=gsk_...      # gratis en console.groq.com
python3 websocket_bridge.py
```

El ESP32 lo encuentra por mDNS, sin configurar direcciones IP.

## Cambiar de proveedor de IA

Se reordena una lista en `servidor/config.yaml`. Ni el firmware ni el agente
se tocan, y si un proveedor falla en caliente la cadena pasa al siguiente:

```yaml
proveedores:
  llm: [groq, nvidia, mlx]      # gratis · gratis · local sin red
  stt: [groq, mlx-whisper]
  tts: [macos]
```

Soportados: Groq, NVIDIA NIM, Cloudflare Workers AI, Azure OpenAI, Vertex AI,
AWS Bedrock, Anthropic y modelos locales por MLX. Detalles en
`servidor/PROVEEDORES.md`.

## Gestionar herramientas MCP

```bash
cd servidor && python3 mcps_cli.py
```

Menú con los 16 MCP del catálogo, su estado real y qué le falta a cada uno.
Ver `servidor/MCP.md`.

## Notas técnicas

Tres cosas que costaron encontrar y conviene saber:

- **IDF 6.0 eliminó el driver I2C antiguo.** Solo existe `driver/i2c_master.h`.
  Por eso el componente oficial `espressif/es8311` no sirve y el códec está
  reimplementado a mano, con la secuencia de registros del driver de Espressif.
- **El backlight tiene lógica invertida** (0 enciende) y va por PWM para regular brillo.
- **El panel se dibuja espejado en X**, así que el táctil se configura igual
  o los toques salen invertidos.
