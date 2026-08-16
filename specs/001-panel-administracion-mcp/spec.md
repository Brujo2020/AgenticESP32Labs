# Spec: Panel de administración (web) para el sistema MCP

**Estado**: borrador para revisión — no implementar hasta aprobar.
**Depende de**: `002-sdk-mcp-scaffolding` (para que "añadir MCP" desde el panel
tenga algo que llamar) y de un endpoint de control en el servidor (fuera de
esta spec, se añade en `plan.md`).

## Por qué

Hoy administrar el servidor (`servidor/`) significa SSH al EC2, editar YAML a
mano y relanzar procesos. Es el punto de mejora #1 y #2 de `../MEJORAS.md`.
El objetivo del proyecto incluye explícitamente: interfaz moderna, plug and
play, drag and drop, para gobernar no solo el backend sino los MCP — verlos,
añadirlos, organizarlos — y los ajustes del sistema (proveedores, credenciales,
transportes), corriendo en el mismo AWS.

## A quién sirve

Un único usuario administrador (Mario) desde cualquier navegador, en LAN o
por internet, sin instalar nada aparte del propio panel.

## Historias de usuario

1. **Como administrador**, entro a `https://<host>/` y veo de un vistazo:
   qué proveedor LLM/STT/TTS está activo en cada cadena, qué MCP están vivos,
   cuáles fallaron al arrancar y por qué, y si el ESP32 está conectado.
2. **Como administrador**, arrastro un MCP del catálogo (Blender, Unity,
   filesystem, memoria…) a "activos" y se activa sin tocar YAML ni reiniciar
   el proceso completo — solo ese MCP.
3. **Como administrador**, creo un MCP nuevo desde el panel (formulario o
   plantilla, ver spec 002) sin escribir el YAML a mano.
4. **Como administrador**, reordeno con drag-and-drop la cadena de respaldo
   de proveedores (`llm: [bedrock, groq, nvidia, mlx]`) y el cambio aplica
   en caliente.
5. **Como administrador**, edito credenciales (API keys) en un formulario con
   los valores enmascarados, no en texto plano en un `.yaml` en el repo.
6. **Como administrador**, veo logs recientes y el estado de conexión del
   ESP32 (última vez visto, firmware, RSSI si el protocolo lo manda).
7. **Como administrador**, agrego una máquina nueva (mi Mac, otro PC) como
   "nodo MCP" desde el panel: le doy nombre, transporte y credencial, y
   aparece en el catálogo igual que Blender o Unity (detalle en spec 003).
8. **Como administrador**, todo cambio queda registrado (quién, qué, cuándo) —
   aunque sea un solo usuario, para poder revertir un error de config.

## Alcance

### Dentro
- Panel web servido desde el mismo EC2 que el `websocket_bridge.py`.
- Lectura y escritura de configuración de proveedores, MCP activos, catálogo
  de MCP, credenciales (con máscara), transportes.
- Vista de estado en vivo: proceso por MCP, último error, latencia del ESP32.
- Autenticación (mínimo: usuario/contraseña o token; ver plan.md para nivel).
- Aplicar cambios sin reiniciar todo el servidor cuando el cambio lo permite
  (activar/desactivar un MCP sí; cambiar el puerto del WebSocket no).

### Fuera (de esta spec; puede ser spec futura)
- Editor visual de flujos/automatizaciones entre MCP.
- Multiusuario con roles.
- Móvil nativo (el panel debe ser responsive, no una app aparte).

## Criterios de aceptación

- [ ] Se puede activar/desactivar cualquier MCP del catálogo desde el panel y
      el efecto se ve en `mcps_cli.py --listar` sin haber tocado YAML a mano.
- [ ] Reordenar la cadena de proveedores en el panel cambia el orden real que
      usa el próximo turno del agente, sin reiniciar el proceso.
- [ ] Ninguna credencial se muestra en claro tras guardarla una vez.
- [ ] El panel es alcanzable en `https://` (no `http://` plano) desde fuera de
      la LAN.
- [ ] Si el panel se cae, el servidor de voz (`websocket_bridge.py`) sigue
      funcionando — no son el mismo proceso ni comparten fallo.
- [ ] Añadir un MCP nuevo desde el panel no requiere SSH ni editar archivos
      a mano en ningún paso.

## Preguntas abiertas (a resolver en plan.md)
- ¿Autenticación propia o detrás de algo ya existente (Tailscale, etc.)?
- ¿El panel reinicia sub-procesos de MCP individualmente o el pool entero?
