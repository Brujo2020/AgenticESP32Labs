# Conectar agentes al HUD

El ESP32 deja de ser una pantalla y pasa a ser una **capacidad MCP**: cualquier
agente que hable Model Context Protocol puede pintar en él, avisarte, hablarte
por el altavoz y —con el protocolo v2— pedirte aprobación física.

Todo esto funciona **con el firmware actual**, sin reflashear.

---

## 1. Arrancar el puente

```bash
cd servidor
python3 websocket_bridge.py
```

Debe decir `escuchando en ws://…:8765` y anunciarse por mDNS como
`mario-hud.local`. El ESP32 lo encuentra solo.

## 2. Probar desde la terminal

```bash
cd servidor
chmod +x hud.py

./hud.py estado
./hud.py mostrar maquina "CPU 34%" "RAM 18/32 GB" "BLENDER 41% CPU"
./hud.py decir "el build de unity ha terminado"
./hud.py avisar "PR 42 aprobado" --nivel ok
```

Navega con los botones laterales hasta **MÁQUINA** y verás las líneas.

### Qué destinos hay

**El firmware ya habla protocolo v2** (desde la versión 0.8.0): manda `hola`
al conectar, y a partir de ahí el servidor deja de degradar a los tres
canales fijos y usa vistas declarativas de verdad — hasta 8, con título
libre, acento y badges por fila. `hud_preguntar` también funciona: el HUD
toma la pantalla completa, pinta hasta 3 botones y bloquea hasta que tocas
uno o vence el plazo.

Si el ESP32 corre un firmware más viejo (anterior a esta versión, sin el
handshake `hola`), el servidor sigue detectándolo solo y se degrada a los
tres canales fijos de siempre:

| Destino | Pantalla | Líneas |
|---------|----------|--------|
| `senales` | SEÑALES | 5 |
| `maquina` | MÁQUINA | 7 |
| `forja` | FORJA | 7 |

En ese caso `hud_preguntar`, `hud_preguntar_async`/`hud_consultar` y
`hud_notificar` no funcionan tal cual (ver la tabla de abajo): hace falta
reflashear con el firmware actual.

Con el protocolo v2 pasan a ser 8 vistas con título libre, y se desbloquea
`preguntar`, la aprobación física a pantalla completa.

---

## 3. Conectarlo a Claude Desktop

Edita `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "dispositivo": {
      "command": "python3",
      "args": ["/Users/mramospe/Proyectos/Development/esp32-hud-idf/servidor/mcps/dispositivo.py"],
      "env": { "HUD_BRIDGE": "ws://127.0.0.1:8765" }
    },
    "vigia": {
      "command": "python3",
      "args": ["/Users/mramospe/Proyectos/Development/esp32-hud-idf/servidor/mcps/vigia.py"],
      "env": {
        "HUD_BRIDGE": "ws://127.0.0.1:8765",
        "VIGIA_REPO": "/Users/mramospe/Proyectos/Development/esp32-hud-idf"
      }
    }
  }
}
```

Reinicia Claude Desktop. A partir de ahí puedes pedirle cosas como:

- «Muéstrame el estado de mi repo en el HUD»
- «Avísame en el cacharro cuando termine, y dilo en voz alta»
- «Pon en la pantalla MÁQUINA lo que más CPU esté gastando»

## 4. Conectarlo a Claude Code

```bash
claude mcp add dispositivo -- python3 \
  /Users/mramospe/Proyectos/Development/esp32-hud-idf/servidor/mcps/dispositivo.py
```

---

## Desde otra máquina de la red

`HUD_BRIDGE` acepta cualquier host: el puente escucha en `0.0.0.0`.

```bash
HUD_BRIDGE=ws://192.168.1.42:8765 ./hud.py avisar "deploy terminado"
```

Así un servidor de CI, un cron o un contenedor pueden escribir en el HUD.

**Aviso serio:** el WebSocket va en claro y sin autenticación. Cualquiera en
esa red puede pintar en tu pantalla y hablarte por el altavoz. Para una red
doméstica pasa; para una red de oficina o una demo en cliente, no. Antes de
exponerlo fuera de tu LAN hace falta TLS y un token compartido.

Esto sigue siendo cierto ahora que el servidor esta en una IP publica de
AWS -- el puerto 8765 (voz) no tiene login. El **panel de administracion**
(`servidor/panel_api.py`, puerto 8766) es distinto: ese si pide token
(`PANEL_TOKEN`) para leer o cambiar cualquier cosa. Ver
`servidor/deploy/README.md` para desplegarlo y `specs/001-panel-administracion-mcp/`
para el resto del plan (TLS, historial de cambios, etc. siguen pendientes).

---

## Automatizar con el código de salida

`preguntar` devuelve 0 si tocaste la primera opción, 2 en cualquier otro caso
—incluido el silencio—. Eso permite usar la aprobación física en un script:

```bash
#!/usr/bin/env bash
if ./hud.py preguntar "DESPLEGAR A PRODUCCION?" SI NO --espera 60; then
    ./deploy.sh
else
    echo "cancelado: nadie confirmo en el dispositivo"
fi
```

El silencio cuenta como negativa, nunca como consentimiento.

---

## Herramientas MCP disponibles

| Tool | Con firmware v2 (actual) | Con firmware v1 (antiguo) |
|------|--------------------------|---------------------------|
| `hud_mostrar` | 8 vistas, título libre, acento y badge por fila | 3 destinos fijos |
| `hud_borrar` | funciona | funciona |
| `hud_notificar` | banda superior 4 s, con nivel y color | se ve como texto en la pantalla VOZ |
| `hablar` | funciona | funciona |
| `dispositivo_estado` | funciona | funciona |
| `hud_preguntar` | pantalla completa, hasta 3 botones, bloqueante | no disponible |
| `hud_preguntar_async` + `hud_consultar` | funciona | no disponible |
