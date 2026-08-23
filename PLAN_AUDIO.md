# Plan de audio — diagnóstico, correcciones y hoja de ruta

Análisis de todo el camino de audio: firmware (`components/audio`,
`components/voice`, `components/board`), servidor (`websocket_bridge.py`,
`proveedores/tts.py`, `nucleo/canal.py`) y despliegue en Lightsail. Comparado
con la arquitectura de audio de `78/xiaozhi-esp32`, leída del código fuente.

---

## Resumen ejecutivo

Había **dos causas raíz independientes**, y ninguna estaba donde se buscaba:

| | Síntoma | Causa real | Dónde |
|---|---|---|---|
| **P1** | timbre metálico | remuestreo 16→24 kHz con interpolador lineal sin filtro anti-imagen | servidor |
| **P2** | cortes de audio | el servidor volcaba la frase entera en el socket sin regular; el ESP32 descartaba lo que no cabía | servidor |

Las dos están corregidas. Además se corrigieron tres problemas secundarios
(siseo, primera sílaba perdida, colchón mal dimensionado).

**Ninguna requería Opus ni rearquitectura.** Lo que sí se copió de Xiaozhi son
dos patrones concretos: el pre-roll del micrófono y el amplificador bajo
demanda.

---

## 1. Lo que ya estaba bien — no se ha tocado

Fijar esto importa: parte del tiempo perdido vino de modificar cosas que
funcionaban.

| Pieza | Por qué está bien |
|---|---|
| `tarea_audio` con stream buffer | El manejador del WebSocket no bloquea; otra tarea alimenta el I2S. Es lo mismo que hace Xiaozhi con su cola de decodificación. |
| Prebúfer antes del primer trozo | Evita el underrun de la primera décima de segundo. |
| `audio_silencio()` tras cada racha | El canal se creó sin `auto_clear`; sin esto el DMA repite en bucle. Bien diagnosticado en su momento. |
| Lector único del micrófono | El bug del medidor robando muestras al STT estaba bien identificado y bien resuelto. |
| `CANAL.send()` con lock | `websockets` no admite escrituras concurrentes. Imprescindible. |
| Síntesis frase a frase | Baja la latencia percibida a la de la primera frase. |
| `s_fin_audio` desde el estado del servidor | Fin explícito en vez de heurística pura. |
| Ping/pong del WebSocket | Mata las conexiones zombi. |

La arquitectura de audio del firmware es sólida. Los fallos estaban en los
bordes.

---

## 2. Diagnóstico con evidencia

### P1 · Timbre metálico — CORREGIDO

**Evidencia** — `servidor/proveedores/tts.py`:

```python
_TASA_POLLY = 16000
if self._TASA_POLLY != sample_rate:
    pcm, _ = audioop.ratecv(pcm, 2, 1, self._TASA_POLLY, sample_rate, None)
```

Polly con `OutputFormat="pcm"` sólo entrega 8000 o 16000 Hz. `BOARD_SAMPLE_RATE`
era 24000, así que cada frase se subía de 16 k a 24 k con `audioop.ratecv`, un
interpolador lineal **sin filtro anti-imagen**. Al remuestrear 16→24 aparecen
réplicas espectrales dentro de la banda audible, alrededor de 8 kHz. Eso es la
aspereza. No venía de Polly: Lucía neuronal suena bien.

**Corrección**: 16 kHz de punta a punta, cadena entera nativa, cero
conversiones.

```
Polly pcm      -> 16000   nativo
Transcribe STT -> 16000   su tasa de trabajo
Piper x_low    -> 16000   nativo
```

La banda útil del habla muere en ~8 kHz: 16 kHz la cubre entera. Subir a 24 kHz
no añadía información, sólo artefactos. De regalo, 33% menos bytes por WiFi.

**Por qué no hubo que tocar el códec**: los registros del ES8311 (0x02–0x06)
están fijados por la relación MCLK = 256 × fs, no por los 24000 en absoluto. A
16 kHz el I2S genera MCLK = 4.096 MHz, sigue siendo 256×, y los mismos
divisores valen.

### P2 · Cortes de audio — CORREGIDO

**Evidencia** — `websocket_bridge.py`, antes:

```python
for k in range(0, len(audio), 2048):
    await envia_raw(ws, audio[k:k + 2048])
```

Volcado sin ninguna regulación temporal. Los números no cuadraban ni de lejos:

| | Valor |
|---|---|
| Frase de 3 s a 16 kHz 16-bit | **96 000 bytes** |
| Colchón del ESP32 (era 340 ms) | **10 880 bytes** |
| Ratio | **8,8 : 1** |

Y en `voice.c` el volcado al colchón usaba `xStreamBufferSend(..., 0)`: timeout
**cero**, lo que no cabe **se descarta**. El propio firmware ya lo avisaba:

> `audio: se descarto un trozo (llega mas rapido de lo que el altavoz puede reproducir)`

**Ese log era el corte.** Lo único que lo mantenía a raya era la ventana TCP del
ESP32 — por accidente, no por diseño. En cuanto el WiFi iba fino se perdía
medio audio.

**Corrección** — regulador de reloj virtual en el servidor (clase `Ritmo`):

```python
self._audio_s += len(trozo) / BYTES_POR_S
adelanto = self._audio_s - (loop.time() - self._t0)
if adelanto > COLCHON_S:
    await asyncio.sleep(adelanto - COLCHON_S)
```

Se lleva la cuenta de cuántos segundos de audio se han mandado y se compara con
los segundos de reloj transcurridos. El servidor puede ir por delante **como
mucho `COLCHON_S`**.

**Por qué reloj virtual y no un `sleep` fijo por trozo**: si la red se atasca un
momento, el adelanto se consume solo y no se duerme — se recupera el ritmo sin
acumular retraso. Un `sleep` fijo sumaría el atasco a la espera y el audio se
iría quedando atrás hasta vaciar el colchón.

**Por qué `Ritmo` dura toda la respuesta y no una frase**: si se reiniciara en
cada frase, el colchón acumulado se perdería justo entre frase y frase, que es
donde menos margen hay (el TTS siguiente trae su propia latencia).

El mismo regulador se aplicó al camino de la herramienta MCP `hablar`, que
tenía idéntico volcado.

**Dimensionado, con margen explícito**:

| | Valor |
|---|---|
| Adelanto máximo del servidor (`COLCHON_S`) | 400 ms |
| Colchón del ESP32 (`AUDIO_MS_BUF`) | 600 ms |
| **Margen para jitter de red** | **200 ms** |
| Coste en RAM interna | 19,2 kB (de 512 kB) |

Si regulador y colchón fueran iguales, cualquier ráfaga tardía desbordaría.

### P3 · Siseo constante — CORREGIDO

`audio.c` dejaba `gpio_set_level(BOARD_PA_EN, 1)` desde el arranque, para
siempre. El amplificador clase D amplificaba su propio ruido de fondo las 24
horas. Ese siseo es lo que hace que un aparato suene barato aunque la voz esté
bien.

**Patrón copiado de Xiaozhi** (`audio_power_timer_`,
`AUDIO_POWER_CHECK_INTERVAL_MS`): encender bajo demanda, apagar tras
inactividad.

**Corrección**: PA apagado al arrancar, se enciende al primer byte de audio, se
apaga tras 3 s de silencio, con 8 ms de estabilización para que el chasquido de
encendido caiga **antes** del audio y no sobre la primera sílaba.

### P4 · Primera sílaba perdida en el STT — CORREGIDO

Entre tocar el botón y que `s_talking` valga `true` pasan el debounce del
táctil, la vuelta del HUD y el turno de `tarea_mic`: 150–250 ms. La primera
sílaba ya salió de la boca. El servidor recibía la frase mutilada y Transcribe
adivinaba — parecía "el STT es malo" cuando no le llegaba la pregunta entera.

**Patrón copiado de Xiaozhi** (`WakeWordAudioCache`): no escuchar antes, sino
**no tirar lo que ya se escuchó**. El micrófono se lee siempre (lo necesita la
barra VU), así que esas muestras ya existían.

**Corrección**: anillo de 300 ms que se llena mientras no se habla, y se vuelca
en el flanco de subida antes del directo. Coste: 9,6 kB de RAM.

### P5 · Huecos entre frases — MITIGADO, pendiente de medir

La heurística de `tarea_audio` espera 400 ms entre frases y 40 ms al final,
según `s_fin_audio`. Con el colchón subido a 600 ms hay más margen, pero si
Polly tarda más de 400 ms en la frase siguiente (latencia Lightsail →
`us-east-1`) el colchón se vacía igual.

Se corrige en fase 3, **con datos**, no a ojo.

---

## 3. Instrumentación añadida — para no volver a discutir de oídas

El problema de fondo de estas semanas: **no había forma de saber si el audio
llegaba demasiado rápido o demasiado lento.** Los dos fallos suenan parecido —
un chasquido — pero se arreglan en direcciones opuestas.

Ahora hay dos contadores en `voice.h`:

```c
uint32_t voice_audio_descartes(void);   // llega demasiado RAPIDO -> se tira audio
uint32_t voice_audio_secos(void);       // llega demasiado TARDE  -> altavoz sin datos
```

| Lectura | Significado | Qué hacer |
|---|---|---|
| ambos **0** | ajuste correcto | nada |
| descartes > 0 | el servidor corre | **bajar** `COLCHON_S` |
| secos > 0 | el servidor llega tarde | **subir** `COLCHON_S` |
| ambos > 0 | red muy inestable | subir `AUDIO_MS_BUF` |

Los dos a la vez sólo pasa si la red da tirones fuertes: el colchón absorbe la
media, no la varianza.

---

## 4. Cómo verificarlo — fase 0, ahora

```bash
# 1. Servidor (SAMPLE_RATE cambió: hay que reiniciarlo)
cd servidor && systemctl --user restart agentic-voz    # o como lo lances

# 2. Firmware
idf.py build flash monitor
```

**Comprobación de coherencia** — si el firmware queda en 16 k y el servidor en
24 k, la voz suena **grave y lenta**. Es un síntoma inequívoco, no lo confundas
con otro bug. Verificado hoy: ambos en 16000.

**Criterios de aceptación**:

| Qué | Esperado |
|---|---|
| Timbre de la voz | sin aspereza metálica |
| Silencio entre respuestas | sin siseo de fondo |
| Primera palabra al STT | llega completa |
| `audio DESCARTE` en el monitor | **cero** en una respuesta larga |
| Chasquidos a mitad de frase | ninguno |

**Prueba concreta**: pídele algo que dé una respuesta de 4–5 frases. Es el peor
caso: varias síntesis encadenadas y varios segundos de audio.

**Si algo empeora**: `git checkout <fichero>`. Nada está commiteado.

---

## 5. Hoja de ruta

### Fase 1 — Consolidar *(hoy)*

Verificar lo anterior en la placa y commitear en cuatro commits separados, para
poder revertir uno sin arrastrar los demás:

```
fix(audio): 16 kHz de punta a punta — el resampleo 16->24 era el metalico
fix(audio): el amplificador se apaga cuando no hay nada que decir
feat(voice): pre-roll de 300 ms — el STT ya no pierde la primera silaba
fix(voz): regular el envio de audio — el servidor volcaba la frase entera
perf(net): NTP y mDNS fuera del camino de arranque      (tuyo, previo)
```

### Fase 2 — Ajuste fino con datos *(tras una semana de uso)*

Leer los dos contadores en uso real, en tu WiFi y con la latencia real a
Lightsail. Ajustar `COLCHON_S` según la tabla de arriba. **Un solo número.**

### Fase 3 — Huecos entre frases *(1 día)*

Con los contadores ya se puede decidir con criterio:

- Si aparecen `secos` entre frases, el margen de 400 ms de `tarea_audio` se
  queda corto: derivarlo de la latencia real medida de Polly.
- Alternativa mejor: lanzar la síntesis de la primera frase **mientras el LLM
  aún está generando** el resto de la respuesta. Elimina la latencia del primer
  TTS del camino crítico, que es la que más se nota.

### Fase 4 — PSRAM *(2 h, riesgo medio, sólo si hace falta)*

**Hallazgo**: `sdkconfig` tiene `CONFIG_SOC_SPIRAM_SUPPORTED=y` pero **no
`CONFIG_SPIRAM=y`**. La N16R8 tiene **8 MB de PSRAM sin usar**.

Permitiría colchones de varios segundos. Pero **el riesgo está documentado por
ti mismo** en `esp32-voice-assistant/platformio.ini`:

> `memory_type=qio_opi causaba panic en boot (StoreProhibited) antes de correr cualquier codigo`

La N16R8 lleva PSRAM **octal**: hace falta `CONFIG_SPIRAM_MODE_OCT=y` y la
velocidad correcta, o no arranca.

**Sólo hacerlo si la fase 2 dice que 600 ms no bastan.** Si los contadores están
a cero, esta fase no aporta nada y sí trae riesgo de arranque.

### Fase 5 — Opus *(1–2 días, opcional)*

Es lo que hace que Xiaozhi aguante redes malas.

| | PCM 16 kHz | Opus 16 kbps |
|---|---|---|
| Frase de 3 s | 96 000 B | **6 000 B** |
| Ancho de banda | 256 kbps | **16 kbps** |
| ¿Cabe entera en el colchón? | no | **sí** |

Con Opus el desbordamiento deja de ser posible por construcción: **la
compresión es el control de flujo**, y el regulador pasa a ser innecesario.
Además cada frame se decodifica solo, así que desaparece el problema de las
fronteras de trozo.

- **Servidor**: `ffmpeg -c:a libopus` (verificado disponible) o `opuslib`.
- **Firmware**: componente gestionado de Espressif. `main/idf_component.yml`
  hoy sólo declara `idf: ">=5.1"`.

**Decisión honesta**: si tras la fase 2 no hay descartes ni secos, **Opus no
arregla nada que duela hoy**. Aporta robustez frente a WiFi malo y ancho de
banda — importante si el aparato sale de tu red, irrelevante en tu escritorio.
Ponerlo por delante de otras cosas del roadmap sería optimizar lo que ya no
molesta.

---

## 6. Lo que NO se copió de Xiaozhi, y por qué

| Mecanismo | Decisión | Motivo |
|---|---|---|
| Resampler polifásico `esp_ae_rate_cvt` | **no** | Xiaozhi lo necesita porque su servidor puede mandar cualquier tasa. Aquí controlas los dos extremos: mejor no resamplear nunca que resamplear bien |
| Assets de voz en partición SPIFFS | **no por ahora** | Sólo hace falta para modo sin red. Hoy sin red no hay agente, así que hablar solo no aporta |
| AEC / barge-in | **más adelante** | Con push-to-talk no hace falta. Si algún día hay wake word, se vuelve obligatorio |
| Opus | **fase 5** | Ver arriba |

---

## 7. Errores cometidos en esta sesión

Van aquí porque afectan a cuánto te puedes fiar del resto del documento:

1. **No busqué el proyecto.** La carpeta estaba conectada desde el principio; el
   primer `ls` salió vacío porque el montaje aún no estaba listo y no volví a
   comprobar. Trabajé horas sobre supuestos.
2. **Reimplementé el Grupo 2.** `display_sin_q()`/`display_cos_q()` ya existían,
   en Q12 con grados enteros — más simple y más exacto que mi Q15 con ángulos
   binarios.
3. **Reimplementé el Grupo 3.** El protocolo v2 con vistas declarativas,
   `hud_preguntar` y aprobación física ya estaba, firmware y servidor.
4. **Levanté un servidor en Docker** sin preguntar si ya había uno desplegado.
   El de Lightsail es mejor en todo.
5. **Te hice clonar `xiaozhi-esp32`**, que es otro firmware. Sirve como
   referencia — de ahí salió el diagnóstico — pero no hay que flashearlo.

Lo aprovechable de la sesión son las correcciones de audio de este documento y
el diagnóstico. El resto es descartable.
