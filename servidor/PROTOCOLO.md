# Protocolo ESP32 ↔ servidor (v2.1)

> v2.1 (ola 2, 13/sep/2026) añade identidad y geometria explicitas al `hola`
> para que un segundo cuerpo (M5StickS3) pueda presentarse sin que el
> servidor tenga que adivinar nada. **Es estrictamente aditiva**: todo campo
> nuevo es opcional y cae a los valores de hoy si falta -- ver la tabla de
> compatibilidad al final.

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

El firmware toma la pantalla completa y pinta la pregunta con hasta
`limites.opciones_max` opciones (ver `hola` mas abajo: 3 con botones, 4 con
tactil -- con dos botones fisicos navegar mas de 3 opciones es un mal rato).
Al tocar/pulsar, responde y devuelve el control. Si vence `timeout`, responde
`-1`.

### `notifica` — interrupción

```json
{"t":"notifica","nivel":"warn","txt":"BUILD CAIDO","beep":true}
```

`nivel`: `info` (cian) · `ok` (lima) · `warn` (ámbar) · `error` (rojo). Se pinta
como banda superior durante 4 s sobre la pantalla actual, sin robar navegación.

---

## ESP32 → servidor

### `hola` — handshake al conectar

Bola (firmware actual, sin cambios — sigue siendo válido tal cual):

```json
{"t":"hola","fw":"0.7.0","vistas_max":8,"filas_max":6,"ancho":26}
```

Bola con handshake explícito (v2.1, opcional):

```json
{"t":"hola","fw":"0.8.0","id":"bola","tipo":"bola","w":240,"h":240,
 "entrada":"tactil","vistas_max":8,"filas_max":6,"ancho":26,
 "servicios":["noticias","telemetria","alertas"]}
```

M5StickS3:

```json
{"t":"hola","fw":"0.8.0","id":"stick","tipo":"sticks3","w":135,"h":240,
 "entrada":"botones","vistas_max":6,"filas_max":5,"ancho":21,
 "servicios":["alertas"],"token":"..."}
```

| Campo | Tipo | Notas |
|-------|------|-------|
| `fw` | string | Versión del firmware. Su sola presencia marca el protocolo como v2 (o v2.1 si trae los campos de abajo). |
| `id` | string | `device_id`. Sin él, se asume `"bola"` (compatibilidad, ley 1). |
| `tipo` | string | `"bola"` o `"sticks3"`. Informativo: el servidor decide por `limites`/`entrada`, nunca por este string (ley 5). |
| `w`, `h` | int | Geometría real de la pantalla en px. Por defecto 240×240 (la bola de hoy). |
| `entrada` | enum | `"tactil"` o `"botones"`. Deriva `limites.opciones_max` (ver `pregunta`). Por defecto `"tactil"`. |
| `vistas_max`, `filas_max`, `ancho` | int | Tamaño real de los buffers estáticos del firmware. El servidor **debe** respetarlos: en un MCU no se reserva memoria por mensaje. |
| `servicios` | array | Qué feeds periódicos quiere (`noticias`, `telemetria`, `alertas`). Sin este campo, se asume que los quiere todos (compatibilidad). El Stick pide solo `alertas`: es de bolsillo, y recibir 5 titulares cada ciclo es ruido y gasto de radio. |
| `token` | string | Opcional. Ver «Autenticación» más abajo. |

### Negociación: el firmware declara, el servidor respeta

No hay ida y vuelta. Un dispositivo nuevo no requiere tocar el servidor; un
servidor nuevo no requiere reflashear ningún firmware existente.

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

Desde v2.1 el servidor guarda cada evento **con el `device_id` de origen**
(`Canal.registra_evento`), visible en `dispositivo_estado()`. Con dos cuerpos
conectados a la vez, una tool puede responder "se tocó en el Stick" en vez de
perderlo en un log de texto.

---

## Autenticación (v2.1, opcional)

```json
{"t":"hola", "...": "...", "token":"el-token-compartido"}
```

- El servidor lee `HUD_TOKEN` de `.env`. **Vacío = modo abierto** (el
  comportamiento de hoy, sin cambio).
- Configurado, se compara con `hmac.compare_digest` (tiempo constante: una
  comparación normal filtra por temporización cuánto del token es correcto).
- Si no coincide: `ws.close(4401, "token")` y se registra en el log. Nunca se
  traza el token recibido.
- En el firmware, el token vive en NVS junto al WiFi, provisionado desde el
  portal de configuración. **Ningún literal en el binario.**

Esto es TLS-menos: el WebSocket sigue siendo texto plano. Es una mejora
honesta, no una solución — el salto real a `wss://` con nginx llega en la
ola de operación, donde ya hay dominio.

---

## Tabla de compatibilidad

| Firmware | `hola` | Resultado |
|---|---|---|
| v1 (no saluda) | — | canal `bola`, 3 pantallas fijas por nombre, como siempre |
| v2 actual (bola en producción) | sin `id`/`tipo`/geometría | canal `bola`, 240×240 táctil asumidos, todo igual |
| v2.1 bola | con `id`/`tipo`/geometría explícitos | canal propio, mismos valores pero declarados, no asumidos |
| v2.1 stick | con `id`/`tipo`/`w`/`h`/`entrada`/`servicios` | canal propio, 135×240, botones (opciones_max=3), solo `alertas` |

Estas cuatro filas son también el plan de pruebas: cada una es un caso en
`pruebas/test_compatibilidad_v1.py`.

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
- **Autenticación opcional, no forzada.** Con `HUD_TOKEN` sin configurar (el
  caso de hoy) el WebSocket sigue siendo texto plano abierto en LAN. El token
  de v2.1 sube el costo de entrada, pero el salto real a TLS/`wss://` sigue
  pendiente (ola de operación).
- **Memoria.** 8 vistas × 6 filas × 27 bytes ≈ 1.3 KB de buffers estáticos, sobre
  los 115 KB que ya consume el framebuffer. Los límites del handshake no son
  decorativos: son lo que evita que el firmware se quede sin heap.
