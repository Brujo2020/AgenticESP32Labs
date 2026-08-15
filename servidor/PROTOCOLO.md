# Protocolo ESP32 ↔ servidor (v2)

## Por qué cambia

La v1 era `{"t": tipo, "v": string}`. Un string plano por mensaje. Consecuencia:
cada función nueva exigía un tipo cableado en C, su array fijo (`s_news[]`,
`s_mac[]`, `s_cre[]`) y su `case` en `hud_render()`. **Añadir una función = editar
C, compilar, flashear, rebootear: ~3 minutos.** Ese era el cuello de botella real
del proyecto, no la falta de ideas.

La v2 introduce dos cosas:

1. **Vistas declarativas.** El servidor describe qué pintar; el firmware sabe
   pintar cualquier vista. Función nueva = un `.py`. Cero flasheo.
2. **Canal inverso.** El ESP32 se expone como servidor MCP. El agente puede
   preguntarle cosas al humano y actuar sobre el dispositivo.

La v1 sigue soportada: los tipos `estado`, `texto`, `tu`, `ia` no cambian.

---

## Servidor → ESP32

### `vista` — declara o actualiza una pantalla

```json
{"t":"vista","id":"unity","titulo":"FORJA","acento":"magenta","orden":6,
 "filas":[{"txt":"BUILD OK 4M12S","color":"lime","badge":"3"},
          {"txt":"0 ERRORES 3 AVISOS","color":"grey"}]}
```

| Campo | Tipo | Notas |
|-------|------|-------|
| `id` | string ≤15 | Clave. Reenviar el mismo `id` reemplaza la vista. |
| `titulo` | string ≤10 | Cabecera. Se pinta en `y=18`. |
| `acento` | enum | `cyan` `magenta` `lime` `amber` `ice` `blood` `grey` `white` |
| `orden` | int | Posición en el carrusel. Las fijas ocupan 0–5. |
| `filas` | array ≤6 | Cada fila: `txt` (≤26 chars), `color`, `badge` opcional. |
| `ttl` | int seg | 0 = permanente. >0 = la vista se autodestruye. |

**`txt` se trunca a 26 caracteres, no a 33.** A `y=62` el borde visible del
círculo cae en `x≈225`; empezando en `x=38` con avance de 6 px, 26 chars llegan
a 194 y 31 ya se salen. El firmware trunca igual, pero truncar en origen evita
mandar bytes que nadie verá.

### `vista_borra`

```json
{"t":"vista_borra","id":"unity"}
```

### `pregunta` — human-in-the-loop

```json
{"t":"pregunta","qid":"q7","txt":"BORRAR 40 ARCHIVOS?",
 "opciones":["SI","NO"],"timeout":30}
```

El firmware toma la pantalla completa, pinta la pregunta y hasta 3 opciones como
botones de 48 px. Al tocar, responde y devuelve el control. Si vence `timeout`,
responde `-1`.

### `notifica` — interrupción

```json
{"t":"notifica","nivel":"warn","txt":"BUILD CAIDO","beep":true}
```

`nivel`: `info` (cian) · `ok` (lima) · `warn` (ámbar) · `error` (rojo). Se pinta
como banda superior durante 4 s sobre la pantalla actual, sin robar navegación.

---

## ESP32 → servidor

### `hola` — handshake al conectar

```json
{"t":"hola","fw":"0.4.0","vistas_max":8,"filas_max":6,"ancho":26}
```

El servidor **debe** respetar estos límites: son el tamaño real de los buffers
estáticos del firmware. En un MCU no se reserva memoria por mensaje.

### `respuesta` — contesta a `pregunta`

```json
{"t":"respuesta","qid":"q7","opcion":0}
```

`opcion` es el índice en `opciones`, o `-1` si venció el timeout.

### `evento` — el usuario tocó algo

```json
{"t":"evento","id":"unity","accion":"toque","fila":2}
```

Permite que una vista sea interactiva: tocar una fila dispara una tool en el
servidor. Aquí es donde una vista deja de ser un informe y pasa a ser un mando.

---

## Herramientas MCP expuestas por el dispositivo

El servidor registra un MCP local `dispositivo` en el pool. Desde ese momento el
agente —y cualquier MCP client, incluido Claude Desktop— ve estas tools:

| Tool | Qué hace |
|------|----------|
| `hud_preguntar(pregunta, opciones, timeout)` | **Bloquea** hasta que el humano toca. Devuelve la opción elegida. |
| `hud_mostrar(id, titulo, filas, acento, ttl)` | Crea o actualiza una vista. |
| `hud_borrar(id)` | Elimina una vista. |
| `hud_notificar(texto, nivel, beep)` | Interrumpe con una banda. |
| `hablar(texto)` | TTS proactivo, sin pregunta previa. |
| `dispositivo_estado()` | RSSI, heap libre, uptime, nivel de mic, pantalla activa. |

### Por qué `hud_preguntar` es la pieza importante

Convierte el cacharro en el **canal de aprobación física** de todo el stack
agéntico. El agente va a hacer algo irreversible —`git push --force`, borrar una
carpeta, enviar un correo, lanzar un deploy— y lo pregunta en un dispositivo que
está en tu escritorio, no en una ventana que tienes tapada. Tocas SÍ o NO.

Eso no es una demo de pantalla: es un patrón de control para sistemas autónomos,
y es exactamente la tesis de "el wearable es el órgano de I/O del agente".

### Riesgos asumidos

- **`hud_preguntar` bloquea el bucle de tools del agente.** Por eso `timeout` es
  obligatorio (30 s por defecto) y vencido devuelve `-1`, nunca cuelga.
- **Sin autenticación.** El WebSocket es texto plano en LAN. Cualquiera en la red
  puede pintar tu HUD y hablarte por el altavoz. Aceptable en casa; para demo en
  cliente hace falta TLS + token compartido.
- **Memoria.** 8 vistas × 6 filas × 27 bytes ≈ 1.3 KB de buffers estáticos, sobre
  los 115 KB que ya consume el framebuffer. Los límites del handshake no son
  decorativos: son lo que evita que el firmware se quede sin heap.
