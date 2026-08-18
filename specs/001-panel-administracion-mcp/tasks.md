# Tasks: Panel de administración

Desglose ejecutable de `plan.md`. Orden sugerido; cada tarea es chica y
comprobable por separado.

## Fase 0 — base para no romper nada
- [x] `nucleo/mcp_pool.py`: `activar(nombre)` / `desactivar(nombre)` sin
      reiniciar el proceso completo. Requirio pasar de un `AsyncExitStack`
      COMPARTIDO a uno por servidor (`self.stacks: dict[str, AsyncExitStack]`)
      -- si no, no se puede cerrar uno solo sin arriesgar los demas.
      `sincroniza(config)` compara `tools_to_enable` contra lo conectado y
      activa/desactiva la diferencia; un MCP que falla no rompe al resto
      (mismo criterio que `connect_all`).
- [x] `proveedores/*.py`: `Cadena.recargar(orden, catalogo)` reconstruye
      `miembros` EN EL MISMO objeto (no crea una Cadena nueva), asi que
      `agente.cadena_llm` y el `cadenas` modulo-nivel de `websocket_bridge.py`
      ven el cambio sin que nadie tenga que reasignar la referencia.
      `recargar_cadenas()` lo aplica a las tres (llm/stt/tts) de una vez.
      Se dispara solo: `websocket_bridge.sincroniza_en_caliente()` corre al
      principio de cada turno de voz, justo despues de que
      `Config.recarga_si_cambio()` detecte que el panel escribio config.yaml
      -- "releer en cada turno", tal cual pedia esta tarea, no un mecanismo
      de push aparte. `panel_api.py` ya no dice "reinicio pendiente": los
      endpoints de activar/desactivar MCP y reordenar proveedores devuelven
      `"aplicacion": "en_caliente_proximo_turno"`.
- [x] Formalizar `agentic-voz.service` (systemd) para el bridge actual —
      independiente de todo lo nuevo, corrige la mejora #8. (`servidor/deploy/`)

## Fase 1 — backend del panel (`panel_api/`)
- [x] Esqueleto FastAPI, healthcheck, lee `config.yaml`/`mcp_catalogo.yaml`
      reusando `panel.py`/`mcps_cli.py`/`nucleo/config.py` (no reimplementa el
      parseo). (`servidor/panel_api.py`)
- [x] Endpoints de solo lectura: `/api/estado`, `/api/mcp`, `/api/proveedores`,
      `/api/claves`. Falta exponer los ultimos errores de arranque en vivo
      (hoy solo se ve al llamar `/api/mcp/probar`, no persistido).
- [x] Endpoints de escritura: activar/desactivar MCP, reordenar cadena de
      proveedores, credenciales, crear MCP nuevo. Escriben el YAML y se
      aplican en caliente en el bridge de voz sin reiniciar nada (ver Fase 0).
- [x] `.env` + resolución de credenciales, endpoint que guarda sin devolver
      el valor en claro (reusa `nucleo/entorno.py`, ya existente).
- [x] Historial de cambios (`config_historial.jsonl`), un log por escritura
      (`_historial()` en cada endpoint de PUT/POST/DELETE, `GET /api/historial`
      para leerlo). JSONL append-only, recorte a las ultimas 500 lineas,
      fuera de git (ver `.gitignore`).
- [x] Auth: token unico (`PANEL_TOKEN`) por header `Authorization: Bearer`.
      **Simplificado de plan.md**: sin cookie de sesion firmada todavia (el
      front guarda el token en `localStorage` y lo manda en cada request) —
      suficiente para un solo usuario detras de TLS, pero mas debil que una
      cookie httpOnly. Revisar si se expone mas alla de uso personal.

## Fase 2 — frontend (`panel_web/`)
- [x] Layout: panel de estado (MCP vivos/caidos, proveedores activos).
      Falta el estado de conexion del ESP32 en si (no hay endpoint que lo
      exponga todavia; `nucleo/canal.py` sabe si hay dispositivo conectado
      pero panel_api no lo consulta aun).
- [x] Catalogo con drag-and-drop hacia "activos". Dos zonas de drop
      (`#mcp-activos` / `#mcp-disponibles`, mismo patron HTML5 DnD que las
      cadenas de proveedores); soltar una tarjeta en la otra zona llama a
      `/api/mcp/{nombre}/activar` o `/desactivar` segun donde caiga.
- [x] Reordenar cadenas de proveedores con drag-and-drop nativo (HTML5 DnD,
      sin libreria — mas liviano que dnd-kit para este alcance).
- [x] Formulario de credenciales (campos enmascarados).
- [x] Vista de logs recientes (consume el WebSocket de estado del panel_api).
      `GET /api/ws/estado` empuja `{estado, historial}` cada 3s (token por
      query param, el navegador no deja mandar Authorization al abrir un WS);
      el front cae a REST + polling de 10s si el WS falla 3 veces seguidas
      (proxy sin soporte ws, etc).
- [x] Pantalla "nodo MCP nuevo" — ya no es placeholder: llama a
      `sdk_mcp.generador.crear_mcp` (002 quedo resuelto en la misma pasada).

## Fase 3 — despliegue
- [x] `agentic-panel.service` (systemd), `Restart=on-failure`. (`servidor/deploy/`)
- [x] Plantilla de nginx + pasos de certbot en `servidor/deploy/README.md`.
      **Falta aplicarlo en el EC2 real** — esto es la receta, no la ejecucion.
- [ ] Verificar criterio de aceptacion en el EC2 real (matar `agentic-panel`
      a mano y confirmar que `agentic-voz` sigue). No se puede probar desde
      aqui — requiere desplegar primero.

## Fase 4 — documentación
- [x] Actualizar `MCP.md`/`CONECTAR_AGENTES.md` con "asi se administra desde
      el panel" como primera opcion, y mencion del WS de estado + historial.
- [ ] `claude/servidor-backend.md` (memoria del proyecto): apuntar la URL del
      panel y cómo entrar una vez desplegado. **Sigue pendiente**: requiere la
      URL real de despliegue (EC2), que no existe todavia en este repo.
