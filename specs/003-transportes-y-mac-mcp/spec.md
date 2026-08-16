# Spec: Bluetooth como transporte + el Mac como MCP de primera clase

**Estado**: borrador para revisión — no implementar hasta aprobar.
**Depende de**: `002-sdk-mcp-scaffolding` (el Mac se registra como MCP con la
misma base común que cualquier otro).

## Por qué

Puntos de mejora #4 y #5 de `../MEJORAS.md`. Hoy el ESP32 solo tiene un
transporte (WebSocket sobre WiFi) y el Mac aporta datos vía MCPs sueltos, pero
no es "una capacidad conectable" con su propio ciclo de vida gestionado desde
el panel. El objetivo del proyecto pide explícitamente: conectar por
Bluetooth, "hacer cosas" en el Mac desde el sistema, y poder hablar por el
ESP32 desde el Mac.

## Historias de usuario

1. **Como usuario**, si el WiFi no está disponible (o quiero corto alcance sin
   red), el ESP32 se sigue pudiendo usar por Bluetooth/BLE hacia un
   dispositivo cercano (el Mac, un teléfono) que hace de puente al servidor.
2. **Como usuario**, agrego mi Mac como "nodo MCP" desde el panel (spec 001):
   nombre, cómo se conecta (agente local corriendo en el Mac), y a partir de
   ahí el agente en AWS puede pedirle cosas al Mac (abrir una app, leer un
   archivo, correr Blender/Unity ya conectados localmente) igual que le pide
   cosas a cualquier otro MCP.
3. **Como usuario**, desde el Mac (terminal, o el propio Claude Desktop) puedo
   decir "avísame por el HUD" o "dilo por el altavoz del ESP32" y sale sonido
   del dispositivo — hoy esto ya funciona vía `hud.py` apuntando al
   WebSocket público; con Bluetooth debe funcionar también sin pasar por
   internet cuando el Mac y el ESP32 están en la misma sala.
4. **Como usuario**, veo en el panel si el Mac está conectado ahora mismo, con
   qué transporte (WiFi hacia AWS, o Bluetooth local), y puedo forzar cuál usar.

## Alcance

### Dentro
- Capa de transporte en el firmware (`components/net` o nuevo `components/bt`)
  que añade BLE junto al WebSocket existente, sin quitar el segundo.
- Un "puente" (podría ser un proceso ligero corriendo en el Mac, ver plan.md)
  que hable BLE con el ESP32 y, del otro lado, se registre como nodo MCP
  usando la base común de la spec 002.
- El Mac como MCP: expone las tools que ya existen (`mac.py`, `sistema.py`)
  más lo que se agregue, pero ahora con presencia/estado gestionado.
- Decisión de qué transporte usar cuando hay varios disponibles (política
  simple: preferir el local/Bluetooth si está, si no WiFi/AWS — igual que la
  cadena de proveedores hoy degrada sola).

### Fuera
- Emparejamiento BLE con más de un dispositivo a la vez (multi-host).
- Streaming de audio completo por BLE (ancho de banda limitado; puede quedar
  para texto/comandos cortos primero, audio como fase 2 si el ancho de banda
  lo permite).

## Criterios de aceptación

- [ ] El ESP32 puede recibir al menos un comando (mostrar vista, notificar)
      por BLE sin pasar por el WebSocket ni por AWS.
- [ ] El Mac aparece en el panel (spec 001) como nodo MCP con estado
      conectado/desconectado en tiempo real.
- [ ] "Habla por el ESP32 desde el Mac" funciona tanto vía WiFi/AWS (como
      hoy con `hud.py`) como, si están cerca, vía Bluetooth sin depender de
      que el servidor de AWS esté arriba.
- [ ] Si BLE falla, el sistema cae a WiFi/AWS sin intervención manual (misma
      filosofía de degradación que la cadena de proveedores).
- [ ] Documentado en `CONECTAR_AGENTES.md` cómo emparejar un Mac nuevo.

## Preguntas abiertas
- ¿El puente BLE corre como daemon en el Mac (LaunchAgent) o se lanza a mano?
- ¿Seguridad del emparejamiento BLE (PIN, whitelist de MAC) — mínimo viable
  para no dejarlo tan abierto como el WebSocket actual?
