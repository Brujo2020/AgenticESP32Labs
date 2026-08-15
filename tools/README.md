# Simulador del HUD

`simulador.html` reproduce la pantalla circular del ESP32 en el navegador:
mismo framebuffer RGB565, misma fuente 5x7, mismas primitivas de dibujo y
el mismo bucle a 30 fps que `main.c`.

Ábrelo sin más:

```bash
open tools/simulador.html
```

No necesita servidor, ni build, ni dependencias. Es un solo fichero.

## Para qué sirve

El ciclo de iterar la interfaz en la placa es de unos tres minutos: editar C,
compilar, flashear, ciclo de alimentación. Aquí es de un segundo. Se usa para
decidir posiciones, colores y tamaños antes de tocar el firmware.

También lleva overlays de diagnóstico que en la placa no se pueden ver:

- **Área visible del círculo** (r=118) y el borde físico (r=120)
- **Zonas táctiles reales**, con el margen de 14 px que añade `ui_dentro()`
- **Filas de AJUSTES frente a las bandas táctiles**, que fue como se encontró
  el bug que hacía REJILLA inalcanzable

La casilla **"Reproducir los bugs antiguos"** vuelve al comportamiento anterior
a v0.7, para comparar de un vistazo qué cambió.

## Regla importante: mantenerlo en sincronía

El simulador vale exactamente lo que valga su fidelidad. Si `hud.c` y este
fichero divergen, deja de ser una herramienta y pasa a ser un dibujo bonito
que engaña.

Cuando cambies geometría en el firmware, cámbiala aquí. Los valores que
**tienen que coincidir**:

| Firmware (`hud.c`) | Simulador |
|--------------------|-----------|
| `MARGEN_IZQ`, `MARGEN_DER`, `TXT_IZQ` | mismas constantes |
| `B_PREV`, `B_NEXT` | `B_PREV`, `B_NEXT` |
| `boton_accion()` | `botonAccion()` |
| bandas táctiles de AJUSTES | `BANDAS_FIX` |
| paleta de `display.h` | constantes `C_*` |
| `ancho_seguro()` | `anchoSeguro()` |

La fuente (`font5x7.c`) y las primitivas de `display.c` están portadas literal;
esas rara vez cambian.

## Subirlo a la web

Es un único HTML autocontenido, así que vale cualquier hosting estático.

**GitHub Pages** — lo más directo, ya que el repo está en GitHub:

```bash
git subtree push --prefix tools origin gh-pages
```

Queda en `https://brujo2020.github.io/AgenticESP32Labs/simulador.html`.

**Netlify o Vercel** — arrastrar la carpeta `tools/` a su interfaz, o:

```bash
npx netlify-cli deploy --dir=tools --prod
```

**Servirlo en la red local** para verlo desde el móvil o una tablet:

```bash
cd tools && python3 -m http.server 8080
```

No hay nada sensible dentro: ni credenciales, ni rutas, ni llamadas a red.
Todo el estado es simulado y vive en memoria.

## Ideas para más adelante

- Conectarlo al puente por WebSocket y que renderice **lo que el ESP32 está
  mostrando de verdad**, en vez de datos simulados. Con eso se convierte en un
  monitor remoto del dispositivo, útil para una demo sin pasar el cacharro de
  mano en mano.
- Exportar el fotograma a PNG para meterlo en slides.
- Grabar un GIF de una secuencia, para documentación.
