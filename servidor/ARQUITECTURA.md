# `server/brain` — arquitectura y patrones

> Cómo está organizado el cerebro y **qué reglas sigue el código nuevo**.
> Las leyes del proyecto están en `configs/constitution/kernel.md`.

## Mapa

```
server/brain/
├── websocket_bridge.py   puente: acepta dispositivos, orquesta voz, difunde
├── panel_api.py          panel web (FastAPI, :8766)
├── noticias.py           titulares por RSS
├── telemetria.py         estado del PC y apps creativas
│
├── nucleo/               LÓGICA. Sin dependencias del transporte.
│   ├── canal.py          un Canal por dispositivo + RegistroDispositivos
│   ├── foco.py           SuperPower: estado de atención y política de aviso
│   ├── agente.py         el agente y su bucle
│   ├── mcp_pool.py       pool de servidores MCP
│   ├── guardia.py        límites, cuotas y aprobaciones
│   ├── config.py         configuración
│   └── entorno.py        variables de entorno
│
├── proveedores/          CADENAS CON DEGRADACIÓN (STT, LLM, TTS)
│   ├── base.py           interfaz común + ErrorProveedor
│   ├── stt.py            Transcribe, Groq, faster-whisper (local, español)
│   ├── llm.py
│   └── tts.py
│
├── mcps/                 HERRAMIENTAS que el agente puede usar
│   ├── clima.py  consulta.py  dispositivo.py  mac.py  sistema.py  vigia.py
│
└── pruebas/              suite propia, sin pytest ni red
    └── todas.sh
```

## Los seis patrones que sigue este código

### 1. El núcleo no conoce el transporte
`nucleo/` no importa `websockets` para decidir nada. `foco.py` es el caso
extremo: **no hace I/O en absoluto**, recibe señales y devuelve decisiones.

*Por qué:* la política de interrupción es la regla de negocio más delicada del
producto. Mezclada con el transporte, acabas sin saber por qué el cacharro
habló en mitad de una reunión.

### 2. El reloj se inyecta
Todo lo que mide tiempo recibe su reloj por parámetro (`MotorFoco(reloj=...)`).

*Por qué:* una jornada de ocho horas se prueba en milisegundos. Sin esto,
probar "180 minutos de foco" significa esperar 180 minutos — es decir, en la
práctica, **no probarlo nunca**.

### 3. Las reglas se escriben como datos, no como escaleras de `if`
`POLITICA` en `foco.py`, `REGISTRO_STT` en `stt.py`.

*Por qué:* una tabla se lee de un vistazo y se discute. Quince `if` anidados,
no.

### 4. Degradar, nunca romper
Un proveedor que no está disponible devuelve `disponible() == False` y la
cadena pasa al siguiente. Una librería ausente **no impide arrancar**.

*Por qué:* este sistema corre sin supervisión. Prefiere un TTS peor a un
silencio.

### 5. Todo lo que crece tiene tope
Bandejas, historiales, cachés: tope duro y documentado (`tope_bandeja`,
`tope_historial`).

*Por qué:* 909 MiB y ya con swap. Este proceso corre semanas seguidas.

### 6. Toda escritura al socket pasa por el lock de su canal
Nunca se escribe al `ws` crudo. Siempre `Canal.send()` del canal dueño.

*Por qué:* `websockets` no admite escrituras concurrentes. Es la causa conocida
de los cortes de audio y ya se pagó una vez (`PLAN_AUDIO.md`).

## Pruebas

```bash
bash server/brain/pruebas/todas.sh     # sin pytest, sin red, sin hardware
```

Convenciones de la suite:

- Un fichero `test_*.py` por área; se autodescubren.
- `importlib` para cargar el módulo **fresco**, sin arrastrar estado de otra
  prueba.
- Dobles de prueba propios (`ESP32Falso`, `Reloj`) en vez de librerías de
  mocking.
- Salen por `sys.exit(1)` si algo falla, para que `todas.sh` los sume.

**Una prueba nueva debe correr sin red, sin hardware y en milisegundos.** Si
necesita dormir, el reloj está mal inyectado.

## Añadir cosas

**Un MCP nuevo:** un fichero en `mcps/` siguiendo `clima.py` (hereda de
`MCPBase`, que ya envuelve el manejo de errores). Queda disponible en **ambos**
cuerpos sin tocar firmware.

**Un proveedor nuevo:** una clase en `proveedores/` con `disponible()` y su
método de trabajo; se registra en el `REGISTRO_*` del módulo.

**Una función nueva:** *un `.py` en el servidor, **no** un tipo de mensaje
nuevo en el protocolo* (ley 2). El protocolo `t:*` v2 es deliberadamente
pequeño y se mantiene así.
