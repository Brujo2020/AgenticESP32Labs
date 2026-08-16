# Tasks: Panel de administración

Desglose ejecutable de `plan.md`. Orden sugerido; cada tarea es chica y
comprobable por separado.

## Fase 0 — base para no romper nada
- [ ] `nucleo/mcp_pool.py`: exponer `activar(nombre)` / `desactivar(nombre)`
      sin reiniciar el proceso completo (hoy el pool se arma una vez al
      iniciar el bridge).
- [ ] `proveedores/*.py`: releer la cadena en cada turno en vez de fijarla al
      arrancar (o exponer `recargar_cadena()`).
- [ ] Formalizar `agentic-voz.service` (systemd) para el bridge actual —
      independiente de todo lo nuevo, corrige la mejora #8.

## Fase 1 — backend del panel (`panel_api/`)
- [ ] Esqueleto FastAPI, healthcheck, lee `config.yaml`/`mcp_catalogo.yaml`
      reusando `nucleo/config.py` (no reimplementar el parseo).
- [ ] Endpoints de solo lectura: estado de MCP activos, cadena de proveedores,
      últimos errores de arranque (lo que hoy imprime `mcps_cli.py --listar`).
- [ ] Endpoints de escritura: activar/desactivar MCP, reordenar cadena de
      proveedores — llaman a lo hecho en Fase 0.
- [ ] `.env` + resolución de credenciales, endpoint que guarda sin devolver
      el valor en claro.
- [ ] Historial de cambios (`config_historial.jsonl`), un log por escritura.
- [ ] Auth: token/contraseña + cookie de sesión firmada.

## Fase 2 — frontend (`panel_web/`)
- [ ] Layout: panel de estado (MCP vivos/caídos, proveedores activos, ESP32
      conectado sí/no).
- [ ] Catálogo con drag-and-drop hacia "activos".
- [ ] Reordenar cadenas de proveedores con drag-and-drop.
- [ ] Formulario de credenciales (campos enmascarados).
- [ ] Vista de logs recientes (consume el WebSocket de estado del panel_api).
- [ ] Pantalla "nodo MCP nuevo" — placeholder hasta que exista `002` con el
      generador real detrás.

## Fase 3 — despliegue
- [ ] `agentic-panel.service` (systemd), `Restart=on-failure`.
- [ ] nginx: TLS (certbot), reverse proxy a panel_api y, si aplica, `wss://`
      hacia el 8765.
- [ ] Verificar criterio de aceptación: panel_api caído no tumba el bridge de
      voz (matar un proceso a mano y confirmar que el otro sigue).

## Fase 4 — documentación
- [ ] Actualizar `MCP.md`/`CONECTAR_AGENTES.md` con "así se administra desde
      el panel" como primera opción, dejando el flujo YAML/CLI como
      alternativa para quien prefiera terminal.
- [ ] `claude/servidor-backend.md` (memoria del proyecto): apuntar la URL del
      panel y cómo entrar una vez desplegado.
