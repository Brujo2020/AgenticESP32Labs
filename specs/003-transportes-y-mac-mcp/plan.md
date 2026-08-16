# Plan: Bluetooth + Mac como MCP

## Diseño

- **Firmware**: nuevo componente `components/bt/` (BLE GATT), en paralelo a
  `components/voice/` (WebSocket). Mismo protocolo v2 ya definido en
  `servidor/PROTOCOLO.md` (`vista`, `notifica`, `pregunta`...) serializado
  sobre BLE en vez de JSON-por-WS cuando el payload es pequeño (comandos,
  notificaciones); audio queda para una fase 2 si el ancho de banda alcanza.
- **Puente en el Mac**: proceso ligero (`servidor/puente_bt.py`, usa la base
  común de `002-sdk-mcp-scaffolding`) que habla BLE con el ESP32 local y, del
  otro lado, se comporta como un MCP más frente al agente — reutiliza
  `mcps/mac.py` y `sistema.py` ya existentes como sus tools.
- **Política de transporte**: en el ESP32, preferir BLE si hay un puente
  emparejado cerca; si no, WiFi/AWS — misma filosofía de degradación que
  `proveedores` (`bedrock > groq > nvidia > mlx`).
- **Presencia en el panel** (spec 001): el puente del Mac reporta su estado
  igual que cualquier MCP (vivo/caído) vía el mismo `mcp_pool`.

## Riesgos
- BLE en el ESP32-S3-1.28-BOX no está probado en este proyecto — primer paso
  real es un spike de conectividad antes de comprometerse a fechas.
- Seguridad de emparejamiento: mínimo viable = whitelist de MAC address en el
  firmware; PIN si el estándar BLE de la placa lo da fácil.
