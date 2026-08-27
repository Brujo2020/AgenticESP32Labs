# Guión de demo — Asistente ESP32

Orden pensado para que cada paso suba la apuesta. Lo fuerte va al final.

## Antes de empezar (5 min)

- [ ] Hotspot del iPhone encendido **antes** de enchufar la placa
- [ ] `sudo systemctl status agentic-voz agentic-panel` → los dos `active (running)`
- [ ] `venv/bin/python3 diagnostico.py` → todo OK, o sabés qué falta
- [ ] Panel abierto en el navegador, pestaña **Agentes**, perfil **Guardian** el primero
- [ ] Volumen de la placa arriba (panel → Dispositivo → Volumen)

---

## 1. "Es un reloj" (30 s)

Dejalo hablar solo. Hora real de Chile, clima de Santiago, titulares de NTT DATA.
No digas nada todavía — que crean que es eso.

## 2. "Ah, además le puedo pedir cosas" (1 min)

Preguntale algo por voz. Que use una herramienta de verdad: el clima.
El punto acá no es que responda, es que **consultó una API en vivo** para hacerlo.

## 3. "Y le puedo dar cualquier fuente de datos" (1 min)

Panel → **Consulta** → pegá la URL de una API pública.
El agente la trae, la cuenta en español natural y la dice en voz alta.
**Frase clave:** "no le programé esta API, se la di en el momento".

## 4. El momento (2 min) ← ESTO ES LA DEMO

Pedile algo irreversible. El agente **no lo hace**: primero pregunta en la pantalla.

```
agente → confirmar_accion("¿BORRAR EL INFORME?")
       → la pantalla pregunta, con dos opciones físicas
       → tocás SÍ  → se ejecuta
       → no tocás  → NO se ejecuta, y lo dice
```

Hacelo **dos veces**: una aprobando y otra dejando que expire.
Que vean que sin el dedo humano, el agente no pasa.

**Las tres frases que rematan:**

1. "El agente no puede saltarse esto: el servidor rechaza la acción si la
   aprobación no volvió del dispositivo. No hay camino alternativo."
2. "La pregunta va firmada. Un agente comprometido no puede falsificar una
   aprobación ni reusar el 'sí' de otra pregunta." *(HMAC en `nucleo/guardia.py`)*
3. "Esto es el problema que todos tienen con los agentes: no cómo darles
   permisos, sino cómo quitárselos en el momento justo."

## 5. Cierre: cambio de agente en vivo (1 min)

Panel → **Agentes** → subí **Analista** al primer puesto → guardá.
Sin reiniciar nada, el siguiente mensaje ya usa otro cerebro y otras herramientas.
"Mismo hardware, otro trabajo, cero código."

---

## Si algo falla

| Síntoma | Qué decir | Qué hacer |
|---|---|---|
| No entiende la voz | "el micro sufre con el ruido de sala" | Pasá al panel, que es visual |
| El LLM no responde | "se acabó la cuota diaria de AWS" | Botón **Leer noticias** (no usa LLM) |
| La placa no conecta | — | Hotspot primero, después enchufar |
| Se cae todo | — | `diagnostico.py` te dice qué capa, en voz alta |

**Regla de oro:** si algo falla, no lo debuguees delante de la gente.
Pasá a lo siguiente y volvé después.
