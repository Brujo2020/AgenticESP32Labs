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

**Firmware** — ESP-IDF v6.0, target esp32s3:

```bash
./run.sh          # o en VSCode: Cmd+Shift+B
```

Compila, espera a que aparezca el puerto, flashea y captura el arranque
en `boot.log`.

**Servidor** — en el Mac:

```bash
cd servidor
source ./activar.sh      # crea el venv la primera vez, luego solo lo activa
python3 panel.py
```

`activar.sh` existe porque `python3` a secas puede resolver a otra cosa (por
ejemplo el Python de PlatformIO, que trae su propio paquete `mcp` viejo e
incompatible) según qué más tengas instalado en el Mac. El venv aísla esto.
**Cada vez que abras una terminal nueva para tocar el servidor, primero
`source ./activar.sh`.**

Un solo menú para todo: pon ahí tu `GROQ_API_KEY` (gratis en console.groq.com,
queda en `servidor/.env`, no se sube a git), activa las herramientas MCP que
quieras y prueba que todo responde antes de arrancar el puente de verdad con

```bash
python3 websocket_bridge.py
```

El ESP32 lo encuentra por mDNS, sin configurar direcciones IP.

## Panel de control — claves, proveedores, MCP, todo en un sitio

```bash
cd servidor && python3 panel.py
```

- **Claves de API**: se detectan solas leyendo qué variables usa cada
  proveedor y cada MCP activo; se guardan en `servidor/.env` (con permisos
  0600, fuera de git) en vez de tener que hacer `export` en cada terminal.
- **Proveedores (LLM/STT/TTS)**: reordena la cadena de respaldo sin tocar
  `config.yaml` a mano. Detalles del porqué de cada proveedor en
  `servidor/PROVEEDORES.md`.
- **Herramientas MCP**: el mismo gestor de siempre (`mcps_cli.py`), integrado
  aquí — ver `servidor/MCP.md`.
- **Probar todo**: arranca de verdad las cadenas de proveedores y los MCP
  activos y dice qué quedó listo y qué falta, antes de gastar tiempo
  flasheando o hablándole al HUD.

`python3 panel.py --resumen` da el mismo diagnóstico sin menú, para pegarlo
en un issue o revisar rápido si algo no arranca.

## Notas técnicas

Tres cosas que costaron encontrar y conviene saber:

- **IDF 6.0 eliminó el driver I2C antiguo.** Solo existe `driver/i2c_master.h`.
  Por eso el componente oficial `espressif/es8311` no sirve y el códec está
  reimplementado a mano, con la secuencia de registros del driver de Espressif.
- **El backlight tiene lógica invertida** (0 enciende) y va por PWM para regular brillo.
- **`esp_lcd_panel_mirror(true, false)` no espeja la imagen: la corrige.** Este
  módulo viene cableado al revés y esa llamada lo endereza. El resultado visible
  es framebuffer x=0 → izquierda física, sin espejo. Por eso el táctil va con
  `mirror_x = 0`: con 1, tocar a la derecha activaba el botón de la izquierda.
  Se comprueba mirando el texto en pantalla, que se lee del derecho.
