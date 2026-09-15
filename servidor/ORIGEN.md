# Origen de `server/brain`

Este directorio **no es un vendor externo**: es código propio, portado desde el
proyecto hermano `esp32-hud-idf` (AgenticESP32Labs), donde lleva meses corriendo
en producción sobre un Lightsail Ubuntu contra la placa Spotpear ESP32-S3-1.28-BOX.

## Por qué se trae aquí

AsistenteS3 nació apoyado en `xinnan-tech/xiaozhi-esp32-server`. Ese servidor
resolvía la voz, pero dejaba el lazo de control abierto: no hay forma de que el
admin empuje una pantalla, una notificación o una pregunta al dispositivo. El
servidor de `esp32-hud-idf` ya resuelve exactamente eso (protocolo v2 de vistas
declarativas, ver `PROTOCOLO.md`) y además aporta:

- cadena de proveedores LLM/STT/TTS con degradación automática (`proveedores/`)
- agente con bucle de herramientas y pool de MCP (`nucleo/`)
- catálogo y gestor de MCP (`mcps/`, `mcps_cli.py`, `mcp_catalogo.yaml`)
- panel de administración y diagnóstico (`panel.py`, `panel_api.py`)

## Qué NO se portó

- `venv/`, `__pycache__/`: entorno local.
- `.env`: contiene claves. Cada despliegue crea el suyo; ver `PROVEEDORES.md`.
- `config_historial.jsonl`: historial de una máquina concreta.

## Diferencias de hardware a tener en cuenta

El origen apunta a una placa de pantalla **redonda 240x240 con táctil CST816**.
El M5StickS3 es **135x240 rectangular sin táctil** (KEY1 G11 / KEY2 G12 + IMU
BMI270). Todo lo que en el origen asume geometría circular o coordenadas de dedo
hay que recalibrarlo. El audio, en cambio, es el mismo códec ES8311 sobre el
mismo ESP32-S3: esa parte se porta sin cambios, incluidas las lecciones de
`PLAN_AUDIO.md` del proyecto origen (16 kHz punta a punta, sin resamplear).

## Decisión de arquitectura pendiente de reflejar en la constitución

`configs/constitution/kernel.md` todavía dice "Protocolo Xiaozhi, sin protocolo
propio" y fija `xiaozhi-server` como servidor de voz. Con este traslado esa ley
queda obsoleta y debe reescribirse. El plan acordado es que este cerebro hable
el protocolo Xiaozhi **de cara al dispositivo**, para conservar el firmware
`78/xiaozhi-esp32`, el wake-word ESP-SR, Opus y el OTA, transportando las vistas
v2 dentro de los mensajes MCP del device.
