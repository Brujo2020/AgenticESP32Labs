# Puntos de mejora detectados (revisión 16 ago 2026)

Repaso del estado actual (`servidor/`, firmware, protocolo v2) antes de spec-ear
lo nuevo. Sirve de justificación para las specs en este directorio.

## 1. Gestión de MCP: solo CLI + YAML a mano
- Añadir un MCP hoy = editar `mcp_catalogo.yaml` + `config.yaml` a mano, dos
  archivos, con la trampa ya documentada de "está en tools_to_enable pero no
  en mcp_servers".
- `mcps_cli.py` es un menú de terminal: sirve para probar, no para administrar
  en caliente ni para verlo desde fuera del servidor.
- No hay forma de crear un MCP nuevo desde cero sin copiar un archivo existente
  a mano y adivinar la forma (`mcps/*.py` no tiene plantilla ni generador).

## 2. Sin superficie de administración remota
- Todo el ajuste de proveedores (LLM/STT/TTS), MCP activos y credenciales vive
  en `config.yaml` en el propio EC2. Cambiarlo exige SSH.
- No hay un sitio único para ver qué MCP están vivos, cuáles fallaron al
  arrancar y por qué (`mcps_cli.py --listar` ya sabe decir el motivo, pero
  solo por terminal).

## 3. Seguridad del WebSocket
- Documentado en `CONECTAR_AGENTES.md`: "el WebSocket va en claro y sin
  autenticación". Ahora que el servidor está en una IP pública de AWS
  (`56.125.193.142:8765`) esto ya no es un aviso teórico de red doméstica:
  cualquiera en internet puede hablar por el altavoz del ESP32 o leer/escribir
  el canal si encuentra el puerto.
- No hay TLS (`wss://`) ni token compartido todavía.

## 4. Transportes limitados
- Solo hay un transporte real: WebSocket IP (WiFi). No hay Bluetooth/BLE, así
  que el ESP32 depende de tener WiFi configurado y del servidor accesible por
  IP — no sirve como mando de corto alcance ni como fallback sin red.

## 5. El Mac no es una capacidad MCP en sí mismo
- Hoy el Mac aporta MCPs sueltos (`mac.py`, `sistema.py`) que exponen datos
  del Mac al agente que corre en AWS. No existe el camino inverso limpio:
  "usa el Mac como si fuera un MCP más, descubierto y conectado igual que
  Blender o Unity", con su propio ciclo de vida (arrancar/parar/reconectar)
  gestionado desde el panel.

## 6. Sin catálogo de MCP externos (Unity, Blender, IDEs) unificado
- `blender` y `unity` ya están en el catálogo pero apagados y sin UI para
  activarlos, probarlos ni ver su estado — hay que tocar YAML y relanzar.

## 7. Escalar MCPs propios es manual y repetitivo
- Cada MCP nuevo repite el mismo boilerplate (conexión al bus, registro de
  tools, manejo de errores) sin una capa común ni generador de plantillas.

## 8. Despliegue del servidor no está formalizado
- No hay systemd/servicio, ni receta reproducible de "así se instala en un
  EC2 nuevo" más allá de `PROVEEDORES.md` y `CONECTAR_AGENTES.md` (pensados
  para desarrollo, no para producción).

---

Estas ocho observaciones se convierten en tres specs (SDD, spec-kit style:
spec → plan → tasks) en este directorio:

1. **`001-panel-administracion-mcp/`** — la interfaz web moderna, plug-and-play,
   drag-and-drop, para administrar todo el sistema (proveedores, MCP, ajustes),
   desplegada en el mismo AWS.
2. **`002-sdk-mcp-scaffolding/`** — capa común + generador para crear MCP nuevos
   rápido, de forma ordenada y escalable.
3. **`003-transportes-y-mac-mcp/`** — Bluetooth como transporte adicional del
   ESP32, y el Mac (y otras máquinas) como MCP de primera clase, incluyendo
   hablar por el ESP32 desde el Mac.

Cada spec sigue el flujo SDD: **spec.md** (qué y por qué, sin implementación),
**plan.md** (arquitectura y decisiones técnicas), **tasks.md** (desglose
ejecutable). Se implementa en ese orden y no se toca código antes de tener
`spec.md` aprobado.
