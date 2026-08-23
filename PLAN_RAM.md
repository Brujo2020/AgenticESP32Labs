# PSRAM y reparto de memoria

Complemento de `PLAN_AUDIO.md`. Aquí sólo memoria: qué había, qué se movió,
por qué, y cómo verificar que sirvió.

---

## 1. El hallazgo

`sdkconfig` tenía `CONFIG_SOC_SPIRAM_SUPPORTED=y` pero **no `CONFIG_SPIRAM=y`**.
La N16R8 lleva **8 MB de PSRAM que no se estaban usando**.

Y tu propia nota en `sdkconfig.defaults` ya lo tenía previsto:

> PSRAM DESACTIVADA a proposito. […] Se reactiva mas adelante, cuando haga
> falta para buffers de audio, y con el modo ya verificado.

Es ahora: el colchón de audio subió a 600 ms y el pre-roll añadió otros 10 kB.

---

## 2. Reparto — el principio

La regla que sigue Xiaozhi, y que aquí aplica igual:

> **PSRAM para lo que sólo toca la CPU. RAM interna para lo que toca un DMA.**

No es una preferencia estética. La PSRAM va por un bus externo con caché: los
accesos secuenciales rinden bien, pero un periférico haciendo DMA sobre ella
sufre latencias que se traducen en glitches — en pantalla y en audio.

| Buffer | Tamaño | Dónde | Por qué |
|---|---|---|---|
| Framebuffer `s_fb` | **115 200 B** | **PSRAM** | Sólo lo escribe la CPU. Al panel no se le manda desde aquí |
| Franjas DMA `s_strip[2]` | 38 400 B | **interna** | Es DMA real hacia el SPI del LCD |
| Colchón de audio | 19 200 B | **PSRAM** | `i2s_channel_write` copia a sus propios descriptores |
| Pre-roll del micro | 9 600 B | **PSRAM** | Sólo lo toca `tarea_mic` |
| WiFi + lwIP | ~50 000 B | **PSRAM** | `CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP` |

**Total liberado de RAM interna: ~194 kB.**

Es exactamente el reparto de Xiaozhi con LVGL: buffer de dibujo en PSRAM,
buffer de transferencia en interna.

---

## 3. Por qué el framebuffer en PSRAM no penaliza

Es la duda razonable: la PSRAM es más lenta, ¿no se va a notar en el HUD a
30 fps?

Los dos patrones de acceso al framebuffer son **secuenciales**, que es donde
la caché funciona bien:

| Operación | Patrón | Impacto |
|---|---|---|
| `display_flush` (memcpy por filas) | secuencial | despreciable |
| `display_escala` sobre `W*H` | secuencial | despreciable |
| `display_px` en dibujo | disperso pero disperso *local* | bajo |
| `display_px_glow` | lectura-modificación-escritura dispersa | el peor caso, pero son pocos píxeles |

Si aun así se notara, el arreglo es trivial y está acotado: volver a poner el
framebuffer en interna (una línea) y dejar en PSRAM sólo los buffers de audio,
que no tienen ninguna sensibilidad a la latencia.

**Cómo se mide**: los fps del HUD. Si bajan de forma apreciable, revertir.

---

## 4. Seguridad — esto no puede tumbar el arranque

Tu nota documentaba un panic de boot con PSRAM en el proyecto Arduino:

> `memory_type=qio_opi causaba panic en boot (StoreProhibited) antes de correr
> cualquier codigo`

**Explicación**: `qio_opi` en PlatformIO forzaba **además** el flash a modo OPI.
Aquí el flash sigue en DIO (`CONFIG_ESPTOOLPY_FLASHMODE_DIO`), así que no hay
conflicto de pines. El modo octal de la PSRAM es el correcto para la N16R8 —
la "R8" significa precisamente eso.

Aun así, **nada del firmware depende de que la PSRAM exista**:

```c
s_fb = heap_caps_malloc(..., MALLOC_CAP_SPIRAM);
if (!s_fb) s_fb = heap_caps_malloc(..., MALLOC_CAP_DEFAULT);
```

Los tres buffers piden PSRAM primero y caen a interna si no la hay. El
pre-roll ni siquiera aborta si falla: `preroll_guarda()` y `preroll_envia()`
comprueban el puntero y se quedan quietas — es una mejora del STT, no un
requisito para hablar.

**Rollback**: comentar cuatro líneas en `sdkconfig.defaults` y recompilar. El
firmware vuelve al comportamiento exacto de antes.

---

## 5. Dos opciones de configuración que importan más de lo que parecen

```
CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=4096
CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL=32768
```

**La primera**: las reservas menores de 4 kB siguen yendo a RAM interna. Las
pequeñas y frecuentes sufren más la latencia de la PSRAM, y —más importante—
hay código de drivers e ISR que asume memoria interna sin declararlo. Sin este
umbral aparecen fallos raros y difíciles de atribuir.

**La segunda**: reserva 32 kB de RAM interna que `malloc()` nunca entregará
desde PSRAM. Sin esto, el día que la PSRAM se llene, una reserva interna
crítica (un descriptor DMA, un buffer de ISR) puede fallar **en caliente** —
no en el arranque, sino a mitad de una llamada. Es el tipo de fallo que cuesta
días encontrar.

---

## 6. Instrumentación — `informe_memoria()`

Activar la PSRAM no es una creencia: o se ve el reparto en el log o no se sabe
si sirvió. Se imprime dos veces:

```
[RAM arranque]   antes de reservar nada
[RAM todo listo] tras display, audio, WiFi y voz
```

Qué mirar:

| Línea | Significado |
|---|---|
| `PSRAM NO disponible` | no arrancó. Revisar `sdkconfig.defaults` |
| `interna: N libre` | lo que ganó el reparto |
| **`interna: N minima`** | **el número que importa** |

**La mínima es el peor momento desde el arranque.** Si baja de ~40 kB,
cualquier pico —una reconexión TLS, una vista grande, una ráfaga de audio—
puede quedarse sin memoria en caliente. Es el indicador de si hay margen real
o se está viviendo al límite.

---

## 7. Qué hacer con los ~194 kB liberados

**Nada, de momento.** Y esto es deliberado.

Tener margen no es lo mismo que tener que gastarlo. Un firmware con 200 kB
libres no se cuelga cuando llega el pico raro; uno que gastó hasta el último
byte, sí. La RAM libre *es* el resultado.

Cuando haya una razón concreta, el orden por valor:

1. **Decoder Opus** (fase 5 de `PLAN_AUDIO.md`) — necesita RAM y ahora la hay.
   Deja de ser el argumento en contra.
2. **Colchón de audio mayor** — sólo si los contadores `voice_audio_secos()`
   dicen que 600 ms se quedan cortos. **Con datos, no por si acaso.**
3. **Historial de conversación más largo** — hoy limitado por `VOZ_LINEAS`.
4. **Doble framebuffer** — permitiría dibujar el fotograma siguiente mientras
   el actual viaja al panel. Sólo si el HUD se queda corto de fps.

Lo que **no** hay que hacer: subir búferes "porque hay sitio". Cada byte de
colchón es latencia añadida en el camino de la voz.

---

## 8. Verificación

```bash
idf.py fullclean && idf.py build flash monitor
```

`fullclean` es necesario: cambiar `sdkconfig.defaults` con un `sdkconfig` ya
generado no siempre regenera todo.

**Criterios de aceptación**, en orden:

| # | Qué | Esperado | Si falla |
|---|---|---|---|
| 1 | Arranca | sin panic ni reinicios | comentar las 4 líneas de PSRAM |
| 2 | `[RAM arranque]` | PSRAM con ~8 MB totales | idem |
| 3 | `framebuffer en PSRAM` | aparece en el log | revisar `MALLOC_CAP_SPIRAM` |
| 4 | `colchon de audio … en PSRAM` | aparece | idem |
| 5 | HUD | fluido, sin tirones | mover `s_fb` de vuelta a interna |
| 6 | `[RAM todo listo]` interna mínima | **> 100 kB** | investigar quién consume |
| 7 | Voz | sin cortes ni siseo | ver `PLAN_AUDIO.md` |

El paso 5 es el único con riesgo real de que "funcione pero peor". Míralo con
atención antes de dar esto por bueno.

---

## 9. Estado del árbol

```
M components/display/display.c    framebuffer -> PSRAM (con fallback)
M components/voice/voice.c        colchon y pre-roll -> PSRAM (con fallback)
M main/main.c                     informe_memoria()
M sdkconfig.defaults              PSRAM octal activada
```

Commit sugerido, separado de los de audio para poder revertirlo solo:

```
perf(ram): PSRAM octal activada — framebuffer y buffers de audio fuera de la interna
```
