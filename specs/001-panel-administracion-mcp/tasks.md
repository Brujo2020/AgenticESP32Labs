# Tasks: Panel de administración

Desglose ejecutable de `plan.md`. Orden sugerido; cada tarea es chica y
comprobable por separado.

## Fase 0 — base para no romper nada
- [ ] `nucleo/mcp_pool.py`: exponer `activar(nombre)` / `desactivar(nombre)`
      sin reiniciar el proceso completo (hoy el pool se arma una vez al
      iniciar el bridge). **Diferido**: v1 del panel escribe el YAML y ofrece
      un boton "reiniciar servicio de voz" (systemctl) en vez de hot-reload
      real del pool en memoria. Sigue pendiente para quitar el reinicio.
- [ ] `proveedores/*.py`: releer la cadena en cada turno en vez de fijarla al
      arrancar (o exponer `recargar_cadena()`).
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
      proveedores, credenciales, crear MCP nuevo. Escriben el YAML de una;
      "llaman a lo hecho en Fase 0" queda pendiente en el sentido de aplicar
      sin reiniciar (ver nota de Fase 0).
- [x] `.env` + resolución de credenciales, endpoint que guarda sin devolver
      el valor en claro (reusa `nucleo/entorno.py`, ya existente).
- [ ] Historial de cambios (`config_historial.jsonl`), un log por escritura. **Pendiente.**
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
- [ ] Catalogo con drag-and-drop hacia "activos". **Simplificado**: v1
      activa/desactiva MCP con un switch (mas simple, mismo resultado), no
      arrastrando la tarjeta. El drag-and-drop real esta implementado para
      reordenar las cadenas de proveedores (si aplica ahi el patron).
- [x] Reordenar cadenas de proveedores con drag-and-drop nativo (HTML5 DnD,
      sin libreria — mas liviano que dnd-kit para este alcance).
- [x] Formulario de credenciales (campos enmascarados).
- [ ] Vista de logs recientes (consume el WebSocket de estado del panel_api). **Pendiente** — panel_api no tiene todavia el WebSocket de estado en vivo, solo REST con polling cada 10s.
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
- [ ] Actualizar `MCP.md`/`CONECTAR_AGENTES.md` con "asi se administra desde
      el panel" como primera opcion.
- [ ] `claude/servidor-backend.md` (memoria del proyecto): apuntar la URL del
      panel y cómo entrar una vez desplegado.
