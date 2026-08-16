# Plan: Panel de administración

Implementa `spec.md`. Decisiones técnicas; el "qué y por qué" ya está fijado,
aquí se fija el "cómo".

## Arquitectura

```
                 EC2 Ubuntu (56.125.193.142)
┌──────────────────────────────────────────────────────────┐
│                                                            │
│  websocket_bridge.py  (8765, WS, sin cambios de negocio)  │
│         │                                                 │
│         ├── nucleo/  (agente, mcp_pool, config)           │
│         │                                                 │
│  panel_api/  (nuevo, FastAPI)  ── 8766 o detrás de nginx  │
│         │        │                                        │
│         │        └── lee/escribe config.yaml + catalogo   │
│         │            vía nucleo/config.py (misma fuente   │
│         │            de verdad, no duplicar estado)       │
│         │        └── controla mcp_pool en caliente         │
│         │            (activar/desactivar sin reiniciar)   │
│         │                                                 │
│  panel_web/  (nuevo, estático: build de un front SPA)     │
│         servido por el mismo FastAPI o por nginx           │
│                                                             │
│  nginx  ── TLS (Let's Encrypt), reverse proxy a :8766      │
│         y a :8765 (wss://) si hace falta exponerlo así     │
└──────────────────────────────────────────────────────────┘
```

Dos procesos separados a propósito (bridge de voz vs. panel_api) — criterio
de aceptación "si el panel se cae, la voz sigue" del spec. Comparten el mismo
`config.yaml` y el mismo `mcp_pool` en memoria vía IPC simple (socket unix o
llamada HTTP local del bridge al panel_api, a decidir en detalle al
implementar) — **no** vía reinicio de proceso para cambios que no lo requieren.

## Stack

- **Backend del panel**: FastAPI (Python, coherente con el resto del repo,
  cero lenguaje nuevo que mantener). Expone REST + un WebSocket propio para
  estado en vivo (logs, salud de MCP) — separado del 8765 de voz.
- **Frontend**: SPA moderna con drag-and-drop real (librería tipo dnd-kit),
  un solo build estático servido por FastAPI/nginx — nada de SSR/Node corriendo
  en producción, para no sumar otro proceso a administrar.
- **Auth**: mínimo viable — un token/contraseña única (variable de entorno),
  cookie de sesión firmada. Nada de OAuth ni multiusuario (fuera de alcance
  del spec). Si más adelante se pone detrás de una VPN (Tailscale), este
  login sigue siendo la segunda capa, no se retira.
- **TLS**: nginx + Let's Encrypt (certbot) delante de todo. Sin esto no se
  cumple el criterio de aceptación de `https://`.

## Fuente de verdad de configuración

`config.yaml` y `mcp_catalogo.yaml` siguen siendo los archivos reales — el
panel no inventa una base de datos aparte. Se le añade:
- Un pequeño historial de cambios (quién/qué/cuándo) en un archivo append-only
  separado (`config_historial.jsonl`), no en el YAML mismo.
- Credenciales: dejan de estar en texto plano en el YAML editable a mano;
  se resuelven igual que hoy vía `${VAR}` de entorno, pero el panel las
  administra en un `.env` con permisos 600, nunca las devuelve en claro por
  la API una vez guardadas (solo un placeholder tipo `••••1234`).

## Aplicar cambios sin reiniciar todo

- Activar/desactivar un MCP → parar/arrancar solo ese subproceso en
  `mcp_pool` (ya es un pool de conexiones independientes, ver
  `nucleo/mcp_pool.py`; se le añade control externo).
- Reordenar cadena de proveedores → releer la lista en el próximo turno
  (`proveedores/*.py` ya eligen "el primero disponible"; basta con que lean
  la lista actual en cada turno en vez de una vez al arrancar, o exponer un
  `recargar()`).
- Cambios que sí requieren reinicio (puerto del WebSocket, por ejemplo): el
  panel lo señala explícitamente en la UI en vez de fingir que aplicó.

## Despliegue

- Mismo EC2, mismo `systemd` (ver nota de mejora #8 en `../MEJORAS.md`: esto
  formaliza también el despliegue del bridge, que hoy no tiene servicio).
- Dos unidades: `agentic-voz.service` (bridge) y `agentic-panel.service`
  (panel_api), independientes, cada una reinicia sola si cae (`Restart=on-failure`).
- nginx como único punto de entrada público, TLS ahí, proxy interno por HTTP
  plano a ambos servicios (misma máquina).

## Riesgos / trade-offs
- Compartir `config.yaml` entre dos procesos vivos exige cuidado de
  concurrencia (locks al escribir) — se resuelve con un lock de archivo simple,
  volumen de escrituras es bajo (un humano, no tráfico).
- Meter un SPA nuevo suma una etapa de build; se compensa dejándolo out-of-band
  (se construye y se sube el `dist/`, no se compila en el EC2).
