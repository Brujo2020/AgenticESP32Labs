#!/usr/bin/env python3
"""
Puente de voz ESP32 <-> agente.

Protocolo (ws://0.0.0.0:8765):
  ESP32 -> servidor
      binario            fragmentos de audio PCM 16-bit mono 24 kHz
      {"t":"fin"}        se solto el boton: procesa lo grabado
      {"t":"ping"}       latido
  servidor -> ESP32
      {"t":"estado","v":"listening|processing|speaking|idle|error"}
      {"t":"texto","v":"..."}     lo que se entendio / lo que responde
      binario            audio de respuesta, mismo formato PCM

Ola 1 (brain-multidispositivo): puede haber mas de un dispositivo conectado
a la vez (la bola blanca y el M5StickS3). Cada uno tiene su propio Canal
(nucleo/canal.py) con su propio lock de escritura, sus propias vistas y su
propia sesion de voz -- nunca se comparten. Los servicios periodicos
(noticias, telemetria, alertas) son UNA tarea por proceso, no una por
conexion: antes, con N dispositivos conectados, habia N pollers de RSS
duplicados escribiendo directo al socket sin pasar por el lock del canal.
"""
import asyncio, audioop, hmac, json, os, resource, socket, subprocess, tempfile, time, wave, logging
import websockets

from nucleo.entorno import carga_env
carga_env()   # servidor/.env, si existe — ver panel.py. No pisa el entorno real.

from nucleo import Agente, Config, MCPPool
from nucleo.canal import REGISTRO_DISPOSITIVOS as REGISTRO
from nucleo.guardia import GUARDIA, Rechazo
from nucleo.foco import MOTOR, Aviso, PASA, GUARDA, URGENTE, NORMAL, AMBIENTE, FOCO
from nucleo.ritmo import RITMO
from nucleo.planificador import plan_del_dia, _infiere_modo
from nucleo import historial_foco, preferencias_modo
from proveedores import cadenas_desde_config
from noticias import titulares
from telemetria import lineas_mac, lineas_creativo

# 16000 y no 24000: es la tasa nativa de Polly (pcm), de Transcribe y de las
# voces x_low de Piper. Con 24000 habia que resamplear con audioop.ratecv
# (lineal, sin filtro anti-imagen) y eso era el metalico de la voz.
# Debe coincidir con BOARD_SAMPLE_RATE del firmware.
SAMPLE_RATE = 16000
HOST, PORT = "0.0.0.0", 8765

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("puente")

agente = None
cadenas = {}

# ---- Ola 8 (operacion): salud del proceso, ver comando de control 'salud' ----
# Uptime y contadores de turnos de voz -- lo que /api/salud (panel_api.py)
# necesita para reportar "el puente vive y esto es lo que ha hecho" sin
# depender de leer logs a mano.
_inicio = time.time()
_contadores = {"turnos_ok": 0, "turnos_error": 0, "turnos_ruido": 0}

# Ola 8, tarea "Consumo de proveedores por dispositivo en el panel": mismo
# contador de arriba, pero desglosado por device_id -- el global sirve para
# /api/salud (todo el proceso), este para saber SI FUE la bola o el Stick
# quien gasto la cuota. No se toca proveedores/__init__.py (Cadena.chat/
# transcribir/sintetizar) para no tocar el camino caliente de cada turno de
# voz -- se cuenta aqui, en el unico sitio que ya sabe el device_id Y el
# desenlace del turno.
_contadores_por_dispositivo: dict[str, dict] = {}


def _cuenta(canal_disp, clave: str):
    _contadores[clave] += 1
    d = _contadores_por_dispositivo.setdefault(
        canal_disp.device_id, {"turnos_ok": 0, "turnos_error": 0, "turnos_ruido": 0})
    d[clave] += 1

# Ola ritmo, Fase 2: "disparo del parte del dia al primer 'hola' de la
# mañana" (tasks.md). No es un cron -- se dispara solo cuando alguien de
# verdad conecta, que es exactamente cuando tiene sentido mostrarlo. Guarda
# la fecha (no un booleano) para que un reinicio del proceso a media
# jornada no vuelva a replanificar de mas si ya se hizo hoy.
_ultimo_dia_planificado = ""


async def _pinta_parte_del_dia(canal_disp):
    """cerebro-jornada, los dos pendientes que ese tasks.md dejaba fuera de
    su ola ("vista dedicada en el HUD" + "disparo automatico al primer
    hola", en vez de esperar a que el agente llame a parte_del_dia() por
    voz). No reimplementa mcps/jornada.py::parte_del_dia() como proceso
    aparte -- reusa lo que el bridge YA tiene fresco (_cache_clima,
    _cache_noticias, alimentados por difunde_clima()/difunde_noticias()) en
    vez de volver a pedirlo todo por HTTP en cada conexion de la mañana."""
    if not canal_disp.quiere("noticias") and not canal_disp.quiere("clima"):
        return   # el dispositivo no pidio ninguno de los dos servicios que arma esta vista
    from nucleo import pendientes as _pendientes
    try:
        hoy = await asyncio.to_thread(_pendientes.lista)
        ayer = await asyncio.to_thread(_pendientes.pendientes_de_ayer)
    except Exception as e:
        log.warning("parte del dia: no se pudo leer pendientes.json: %s", e)
        hoy, ayer = [], []
    filas = [f"{len(hoy)} pendiente(s) hoy"]
    if ayer:
        filas.append(f"{len(ayer)} sin cerrar de ayer")
    if _cache_clima:
        filas.append(_cache_clima[-1])
    if _cache_noticias:
        filas.append(_cache_noticias[0][:24])
    try:
        await canal_disp.mostrar("parte", "BUENDIA", filas, acento="lime")
    except Exception as e:
        log.warning("parte del dia: no se pudo pintar en '%s': %s", canal_disp.device_id, e)


async def _asegura_plan_del_dia(canal_disp):
    """Arma el plan de hoy con lo pendiente (nucleo/planificador.py) y
    pinta el parte del dia (cerebro-jornada) LA PRIMERA vez que cualquier
    dispositivo saluda en el dia -- no en cada conexion, y nunca pisa un
    plan de ritmo que el humano ya puso a mano (RF-9)."""
    global _ultimo_dia_planificado
    hoy = time.strftime("%Y-%m-%d")
    if hoy == _ultimo_dia_planificado:
        return
    _ultimo_dia_planificado = hoy

    await _pinta_parte_del_dia(canal_disp)

    if RITMO.plan():
        log.info("ritmo: ya hay un plan puesto para hoy, no se pisa con plan_del_dia()")
        return
    ocultar = _ocultar_calendarios_agenda()
    try:
        bloques = await asyncio.to_thread(plan_del_dia, None, ocultar)
    except Exception as e:
        log.warning("ritmo: plan_del_dia() fallo, sin plan automatico hoy: %s", e)
        return
    if not bloques:
        return
    RITMO.plan_set(bloques)
    log.info("ritmo: plan del dia armado con %d bloque(s) desde pendientes.json", len(bloques))
    if canal_disp.quiere("ritmo"):
        try:
            await canal_disp.notifica(f"plan del dia: {len(bloques)} bloque(s)", "info")
        except Exception as e:
            log.warning("ritmo: no se pudo avisar el plan del dia: %s", e)


async def arranca_agente():
    global agente, cadenas
    config = Config()
    # Cadenas de proveedores: el hyperscaler se elige en config.yaml
    cadenas = cadenas_desde_config(config.data)
    pool = MCPPool()
    agente = Agente(config=config, mcp_pool=pool, cadena_llm=cadenas["llm"])
    await agente.initialize()
    log.info("agente listo — herramientas: %s", agente.herramientas_disponibles())


def normaliza(pcm: bytes) -> bytes:
    """Sube el volumen del audio del microfono antes de mandarlo al STT.

    El microfono de la placa capta bajo: hablando a medio metro, los picos se
    quedan muy por debajo del fondo de escala. Whisper acierta bastante menos
    con audio flojo -- se inventa palabras o devuelve frases sueltas -- y eso
    acaba pareciendo que el modelo contesta cualquier cosa, cuando lo que pasa
    es que no oyo bien la pregunta.

    Se lleva el pico al 80% del rango: suficiente margen para no recortar los
    transitorios (una 'p' o una 't' pegan mucho mas fuerte que el resto) y
    ninguna ganancia si el audio ya venia bien. Tope de x8 para no convertir
    una sala en silencio en un muro de ruido amplificado.
    """
    if not pcm:
        return pcm
    pico = audioop.max(pcm, 2)
    # Log SIEMPRE, incluso cuando no se amplifica: sin esto no habia forma de
    # saber si el STT "entiende mal" o si sencillamente no le esta llegando
    # voz -- picos por debajo de ~200 en int16 (rango 0-32767) son silencio o
    # ruido de fondo, no habla. Una alucinacion tipo "gracias por ver el
    # video" con pico bajo confirma microfono/gate, no problema de STT.
    log.info("nivel de audio del turno: pico=%d (rango 0-32767, ~200=silencio)", pico)
    if pico < 200:            # practicamente silencio: amplificar solo daria ruido
        log.warning("audio del turno es practicamente silencio (pico=%d) -- "
                     "el STT va a fallar o alucinar, esto NO es culpa del STT", pico)
        return pcm
    objetivo = int(32767 * 0.8)
    factor = min(objetivo / pico, 8.0)
    if factor <= 1.05:        # ya venia con buen nivel
        return pcm
    return audioop.mul(pcm, 2, factor)


def pcm_a_wav(pcm: bytes) -> str:
    pcm = normaliza(pcm)
    f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(f.name, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return f.name


def transcribe(wav_path: str) -> str:
    """STT a traves de la cadena de proveedores."""
    c = cadenas.get("stt")
    if c and c.miembros:
        try:
            return c.transcribir(wav_path)
        except Exception as e:
            log.error("cadena STT agotada: %s", e)
            return ""
    return _transcribe_local(wav_path)


def _transcribe_local(wav_path: str) -> str:
    """Respaldo si no hay cadena configurada."""
    try:
        import mlx_whisper
        r = mlx_whisper.transcribe(
            wav_path, path_or_hf_repo="mlx-community/whisper-small-mlx")
        return (r.get("text") or "").strip()
    except Exception as e:
        log.warning("mlx-whisper no disponible (%s), probando whisper", e)
    try:
        import whisper
        model = whisper.load_model("small")
        return (model.transcribe(wav_path, language="es").get("text") or "").strip()
    except Exception as e:
        log.error("sin STT disponible: %s", e)
        return ""


def sintetiza(texto: str) -> bytes:
    """TTS a traves de la cadena de proveedores."""
    c = cadenas.get("tts")
    if c and c.miembros:
        try:
            return c.sintetizar(texto, SAMPLE_RATE)
        except Exception as e:
            log.error("cadena TTS agotada: %s", e)
            return b""
    return _sintetiza_local(texto)


def _sintetiza_local(texto: str) -> bytes:
    """Respaldo si no hay cadena configurada."""
    aiff = tempfile.NamedTemporaryFile(suffix=".aiff", delete=False).name
    raw  = tempfile.NamedTemporaryFile(suffix=".raw", delete=False).name
    try:
        subprocess.run(["say", "-v", "Monica", "-o", aiff, texto], check=True)
        subprocess.run(["afconvert", "-f", "caff", "-d", f"LEI16@{SAMPLE_RATE}",
                        "-c", "1", aiff, raw], check=True)
        with open(raw, "rb") as f:
            data = f.read()
        return data[4096:]          # salta la cabecera CAF
    except Exception as e:
        log.error("fallo el TTS: %s", e)
        return b""
    finally:
        for p in (aiff, raw):
            try: os.unlink(p)
            except OSError: pass


def _en_frases(texto: str, minimo: int = 40) -> list[str]:
    """Parte la respuesta en frases para poder sintetizarlas por separado.

    Es lo que quita la espera larga: en vez de generar TODO el audio y luego
    mandarlo, se sintetiza la primera frase y empieza a sonar mientras se
    prepara la siguiente. El usuario oye una respuesta en un par de segundos
    en lugar de esperar a que termine la frase mas larga.

    'minimo' evita trocear de mas: fragmentos muy cortos suenan entrecortados
    y cada uno cuesta una llamada al TTS, asi que se acumulan hasta tener algo
    con sentido prosodico.
    """
    import re
    # Se corta DESPUES del signo, conservandolo: el TTS necesita el punto o la
    # interrogacion para entonar bien el final de la frase.
    trozos = re.split(r'(?<=[.!?…])\s+', texto.strip())
    frases, actual = [], ""
    for t in trozos:
        if not t:
            continue
        actual = f"{actual} {t}".strip()
        if len(actual) >= minimo:
            frases.append(actual)
            actual = ""
    if actual:
        # La cola corta se pega a la frase anterior en vez de ir suelta.
        if frases and len(actual) < minimo // 2:
            frases[-1] += " " + actual
        else:
            frases.append(actual)
    return frases or ([texto.strip()] if texto.strip() else [])


def _en_lineas(texto: str, ancho: int) -> list[str]:
    """Parte en lineas por palabras: cortar a medias se lee fatal."""
    palabras, lineas, actual = texto.upper().split(), [], ""
    for w in palabras:
        if len(actual) + len(w) + 1 <= ancho:
            actual = f"{actual} {w}".strip()
        else:
            if actual:
                lineas.append(actual)
            actual = w[:ancho]
    if actual:
        lineas.append(actual)
    return lineas


# ============================================================
#  Servicios periodicos: UNA tarea de proceso, repartida a todos los
#  canales vivos -- no una tarea por conexion. Ver docstring del modulo.
# ============================================================

# Ultimo lote enviado de cada feed. Un dispositivo que se conecta a mitad de
# ciclo recibe esto de inmediato (empuja_estado_actual) en vez de esperar
# hasta 15 minutos (noticias) o 5 segundos (telemetria) para su primer dato.
_cache_noticias: list[str] = []
_cache_mac: list[str] = []
_cache_creativo: list[str] = []
_cache_clima: list[str] = []
_clima_geocode = {"ciudad": None, "lat": None, "lon": None}

# Historial de chat (tu/ia) para el panel -- pedido explicito del usuario
# (13/sep/2026): "historial de chat/senales/maquina" en la pagina web, ver
# fn "resumen_feeds" mas abajo. Un solo historial global (no por
# dispositivo): hoy solo el Stick mantiene una conversacion de voz activa a
# la vez, asi que separar por device_id seria complejidad sin beneficio
# real todavia. Tope de 40 lineas para no crecer sin limite en un proceso
# que corre 24/7.
_historial_chat: list[dict] = []


def _chat_cachea(rol: str, texto: str) -> None:
    _historial_chat.append({"rol": rol, "texto": texto})
    del _historial_chat[:-40]


async def envia_a_canal(canal, tipo, valor):
    """Como envia(), pero contra un Canal concreto en vez de un ws suelto.

    Pasa SIEMPRE por Canal.send(), que tiene el lock de ese dispositivo: es
    lo que evita que esta tarea de difusion entrelace sus frames con los del
    audio de voz que puede estar saliendo por el mismo socket en ese momento.
    Si el dispositivo se desconecto entre que se listo y que le tocaba
    escribir, se registra y se sigue: un dispositivo caido no debe tumbar la
    difusion a los demas.
    """
    try:
        await canal.send(json.dumps({"t": tipo, "v": valor}, ensure_ascii=False))
    except Exception as e:
        log.debug("no se pudo enviar '%s' a '%s': %s", tipo, canal.device_id, e)


async def empuja_estado_actual(canal):
    """Al conectar, un dispositivo recibe el ultimo lote de cada feed que
    quiera, sin esperar al siguiente ciclo del difusor correspondiente."""
    if _cache_noticias and pantalla_activa("noticias") and canal.quiere("noticias"):
        await envia_a_canal(canal, "noticias_reset", "")
        for t in _cache_noticias:
            await envia_a_canal(canal, "noticia", t)
    if _cache_mac and pantalla_activa("mac") and canal.quiere("telemetria"):
        await envia_a_canal(canal, "mac_reset", "")
        for l in _cache_mac:
            await envia_a_canal(canal, "mac", l)
    if _cache_creativo and pantalla_activa("creativo") and canal.quiere("telemetria"):
        await envia_a_canal(canal, "creativo_reset", "")
        for l in _cache_creativo:
            await envia_a_canal(canal, "creativo", l)
    if _cache_clima and canal.quiere("clima"):
        try:
            await canal.mostrar("clima", "ATMOS", _cache_clima, acento="ice")
        except Exception as e:
            log.debug("clima: no se pudo empujar estado actual a '%s': %s",
                      canal.device_id, e)


async def _resuelve_geocode_clima(ciudad: str):
    """Cachea lat/lon de la ultima ciudad resuelta -- si no cambia en
    ajustes.yaml, no hay que volver a pegarle a la API de geocoding cada
    ciclo. Misma API que mcps/clima.py (clima_ubicacion), sin depender de
    levantar ese proceso MCP para algo que corre cada 20 min en el mismo
    proceso del puente."""
    if _clima_geocode["ciudad"] == ciudad and _clima_geocode["lat"] is not None:
        return _clima_geocode["lat"], _clima_geocode["lon"]
    import aiohttp
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={ciudad}&count=1"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
    except Exception as e:
        log.warning("clima: geocoding de '%s' fallo: %s", ciudad, e)
        return None, None
    resultados = data.get("results") or []
    if not resultados:
        log.warning("clima: geocoding no encontro '%s'", ciudad)
        return None, None
    r = resultados[0]
    _clima_geocode.update(ciudad=ciudad, lat=r.get("latitude"), lon=r.get("longitude"))
    return _clima_geocode["lat"], _clima_geocode["lon"]


async def difunde_clima():
    """Clima (15/sep/2026, pedido explicito del usuario): antes solo se
    conseguia preguntandolo por voz (mcps/clima.py, bajo demanda). Ahora
    ADEMAS se difunde solo cada 20 min, mismo patron que difunde_noticias --
    a todos los dispositivos vivos que quieran 'clima' (ver SERVICIOS en
    nucleo/canal.py).

    Sin ciudad configurada en el panel (ajustes.yaml: clima.ciudad vacio) no
    hace nada: no se inventa una ubicacion por defecto. La pantalla 'clima'
    tiene que estar activa en el panel, igual que noticias/mac.
    """
    global _cache_clima
    import aiohttp
    while True:
        try:
            cfg = ajustes_actuales().get("clima") or {}
            ciudad = (cfg.get("ciudad") or "").strip()
            if ciudad and pantalla_activa("clima"):
                lat, lon = await _resuelve_geocode_clima(ciudad)
                if lat is not None:
                    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                           "&current=temperature_2m,relative_humidity_2m&timezone=auto")
                    datos_ok = False
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    datos_ok = True
                    except Exception as e:
                        log.warning("clima: consulta fallo: %s", e)
                    if datos_ok:
                        cur = data.get("current") or {}
                        temp = cur.get("temperature_2m")
                        hum = cur.get("relative_humidity_2m")
                        if temp is not None:
                            _cache_clima = [
                                ciudad.upper()[:20],
                                f"{temp}C  HUM {hum}%" if hum is not None else f"{temp}C",
                            ]
                            destinos = [c for c in REGISTRO.vivos() if c.quiere("clima")]
                            for c in destinos:
                                try:
                                    await c.mostrar("clima", "ATMOS", _cache_clima, acento="ice")
                                except Exception as e:
                                    log.warning("clima: no se pudo pintar en '%s': %s",
                                                c.device_id, e)
                            if destinos:
                                log.info("clima actualizado (%s, %s°C) enviado a %d dispositivo(s)",
                                          ciudad, temp, len(destinos))
        except Exception as e:
            log.warning("difunde_clima: %s", e)
        await asyncio.sleep(20 * 60)


async def difunde_noticias():
    """Refresca titulares al arrancar y luego cada 15 minutos, y los reparte
    a todos los dispositivos vivos que los quieran.

    Si la pantalla de noticias esta apagada en el panel, ni se piden los RSS:
    no tiene sentido gastar red y CPU en algo que ningun dispositivo puede
    mostrar.
    """
    global _cache_noticias
    while True:
        try:
            if pantalla_activa("noticias"):
                ts = await titulares(5)
                if ts:
                    _cache_noticias = ts
                    destinos = [c for c in REGISTRO.vivos() if c.quiere("noticias")]
                    for c in destinos:
                        await envia_a_canal(c, "noticias_reset", "")
                        for t in ts:
                            await envia_a_canal(c, "noticia", t)
                    if destinos:
                        log.info("enviados %d titulares a %d dispositivo(s)",
                                 len(ts), len(destinos))
        except Exception as e:
            log.warning("noticias: %s", e)
        await asyncio.sleep(15 * 60)


async def difunde_telemetria():
    """Estado del Mac y de las apps creativas, cada 5 s, a todos los
    dispositivos vivos que los quieran.

    Cada feed se salta si su pantalla esta apagada en el panel: son consultas
    al sistema cada 5 segundos, no vale la pena hacerlas a ciegas.
    """
    global _cache_mac, _cache_creativo
    while True:
        try:
            if pantalla_activa("mac"):
                mac = await asyncio.to_thread(lineas_mac)
                _cache_mac = mac
                for c in REGISTRO.vivos():
                    if not c.quiere("telemetria"):
                        continue
                    await envia_a_canal(c, "mac_reset", "")
                    for l in mac:
                        await envia_a_canal(c, "mac", l)

            if pantalla_activa("creativo"):
                cre = await asyncio.to_thread(lineas_creativo)
                _cache_creativo = cre
                for c in REGISTRO.vivos():
                    if not c.quiere("telemetria"):
                        continue
                    await envia_a_canal(c, "creativo_reset", "")
                    for l in cre:
                        await envia_a_canal(c, "creativo", l)
        except Exception as e:
            log.warning("telemetria: %s", e)
        await asyncio.sleep(5)


async def vigila_alertas():
    """Avisa SIN que nadie pregunte: lluvia proxima y noticias nuevas, en
    TODOS los dispositivos vivos que quieran alertas.

    Es la diferencia entre un cacharro que contesta y uno que te avisa. Todo
    lo demas del HUD es reactivo (tu preguntas, el responde) o pasivo (feeds
    que se refrescan en pantalla y nadie mira). Esto interrumpe: beep, aviso
    en pantalla y, si esta activado, lo dice en voz alta.

    Se controla desde ajustes.yaml (bloque 'alertas') para poder apagarlo en
    una reunion sin recompilar:

        alertas:
          activas: true
          hablar: true
          intervalo_min: 10

    Nunca lanza: una alerta que tumba el puente de voz seria mucho peor que
    una alerta perdida.
    """
    ultimo_titular = None
    aviso_lluvia_dado = False
    intervalo = 600

    while True:
        try:
            aj = ajustes_actuales().get("alertas") or {}
            if not aj.get("activas", True):
                await asyncio.sleep(60)
                continue
            intervalo = max(60, int(aj.get("intervalo_min", 10)) * 60)

            # Cada aviso lleva su urgencia y una clave estable (RF-4/RF-6 de
            # SuperPower, .kiro/specs/superpower/requirements.md): la lluvia
            # es NORMAL (util, no ambiental) y una vez avisada no se repite
            # hasta que escampe; una noticia es AMBIENTE, y la clave es el
            # propio titular para que refrescos identicos no dupliquen.
            avisos = []   # [(Aviso, urgencia_original)]

            # --- lluvia en las proximas horas ---------------------------
            # Open-Meteo sin API key, igual que el MCP de clima. Se mira la
            # probabilidad por hora en vez del "llueve ahora": avisar cuando
            # ya te estas mojando no sirve de nada.
            try:
                import httpx
                url = ("https://api.open-meteo.com/v1/forecast"
                       f"?latitude={ALERTA_LAT}&longitude={ALERTA_LON}"
                       "&hourly=precipitation_probability&forecast_hours=4&timezone=auto")
                async with httpx.AsyncClient(timeout=10) as c:
                    d = (await c.get(url)).json()
                probs = (d.get("hourly") or {}).get("precipitation_probability") or []
                pico = max(probs) if probs else 0
                if pico >= 60 and not aviso_lluvia_dado:
                    horas = probs.index(pico) if pico in probs else 0
                    txt = f"Ojo, {pico} por ciento de lluvia en unas {horas or 1} horas."
                    avisos.append(Aviso(txt, NORMAL, clave="lluvia", origen="alertas"))
                    aviso_lluvia_dado = True
                elif pico < 40:
                    aviso_lluvia_dado = False       # se rearma cuando escampa
            except Exception as e:
                log.debug("alerta lluvia: %s", e)

            # --- titular nuevo -------------------------------------------
            # Solo el primero: si cambia, hay noticia. Comparar la lista
            # entera daria un aviso cada vez que se reordena el feed.
            try:
                ts = await titulares(1)
                if ts and ultimo_titular is not None and ts[0] != ultimo_titular:
                    txt = f"Noticia nueva. {ts[0].capitalize()}."
                    avisos.append(Aviso(txt, AMBIENTE, clave="titular", origen="noticias"))
                if ts:
                    ultimo_titular = ts[0]
            except Exception as e:
                log.debug("alerta noticias: %s", e)

            # --- SuperPower decide: ¿se dice ahora, o se guarda? -----------
            # PASA -> interrumpe como siempre. GUARDA -> a la bandeja de
            # SuperPower; se entrega en el proximo hueco (fin de un descanso,
            # o al volver de HyperFocus/AUSENTE) en vez de perderse.
            a_entregar = [av for av in avisos if MOTOR.propone(av) == PASA]

            # --- entrega: a cada dispositivo vivo que quiera alertas ------
            destinos = [c for c in REGISTRO.vivos() if c.quiere("alertas")]
            for av in a_entregar:
                texto = av.texto
                log.info("ALERTA: %s", texto)
                for c in destinos:
                    try:
                        await c.notifica(texto[:60], "warn", beep=True)
                    except Exception as e:
                        log.warning("alerta: no se pudo notificar a '%s': %s",
                                    c.device_id, e)
                    if aj.get("hablar", True):
                        try:
                            audio = await asyncio.to_thread(sintetiza, texto)
                            await Ritmo().envia(c, audio)
                        except Exception as e:
                            log.warning("alerta: no se pudo hablar en '%s': %s",
                                        c.device_id, e)

        except Exception as e:
            log.warning("vigila_alertas: %s", e)
        await asyncio.sleep(intervalo)


async def _entrega_bandeja(pendientes):
    """Reparte lo que SuperPower tenia guardado, al abrirse un hueco.

    Mismo camino de entrega que vigila_alertas: notifica + voz opcional a
    todo dispositivo vivo que quiera alertas. Separado en su propia funcion
    porque dos sitios lo disparan (el fin de un descanso y la orden manual
    'foco_descanso' del MCP), y las dos deben repartir exactamente igual.
    """
    if not pendientes:
        return
    aj = ajustes_actuales().get("alertas") or {}
    destinos = [c for c in REGISTRO.vivos() if c.quiere("alertas")]
    for av in pendientes:
        for c in destinos:
            try:
                await c.notifica(av.texto[:60], "info", beep=False)
            except Exception as e:
                log.warning("entrega bandeja: no se pudo notificar a '%s': %s",
                            c.device_id, e)
        if aj.get("hablar", True) and destinos:
            try:
                audio = await asyncio.to_thread(sintetiza, av.texto)
                await Ritmo().envia(destinos[0], audio)
            except Exception as e:
                log.warning("entrega bandeja: no se pudo hablar: %s", e)


async def vigila_superpower():
    """El pulso de SuperPower: temporizadores (90/180 min de HyperFocus,
    ausencia, fin de un descanso) que no dependen de que llegue un evento.

    30 s de resolucion es de sobra: los umbrales del motor son de minutos.
    Todo el trabajo de decidir vive en nucleo/foco.py (MotorFoco.tick());
    aqui solo se traduce cada accion a algo que el dispositivo entiende.

    RF-18: aqui mismo, comparando MOTOR.estado antes/despues de cada tick,
    se detecta la transicion a FOCO y se registra en historial_foco.py --
    nucleo/foco.py sigue sin saber que ese historial existe (ver su
    cabecera y la de historial_foco.py).
    """
    while True:
        try:
            estado_antes = MOTOR.estado
            for accion in MOTOR.tick():
                tipo = accion.get("tipo")
                if tipo in ("descanso_sugerido", "descanso_urgente"):
                    destinos = [c for c in REGISTRO.vivos() if c.quiere("alertas")]
                    nivel = "warn" if accion.get("urgencia") == URGENTE else "info"
                    for c in destinos:
                        try:
                            await c.notifica(accion["texto"], nivel,
                                              beep=(nivel == "warn"))
                        except Exception as e:
                            log.warning("superpower: no se pudo avisar a '%s': %s",
                                        c.device_id, e)
                elif tipo == "reset_ofrecido":
                    # Se OFRECE (RF-3): una linea de estado, sin beep. Un
                    # cacharro que regaña por cambiar de ventana no ayuda.
                    for c in REGISTRO.vivos():
                        if c.quiere("alertas"):
                            try:
                                await c.notifica(accion["texto"], "info")
                            except Exception:
                                pass
                elif tipo == "fin_descanso":
                    log.info("superpower: fin de descanso, vuelta a LIBRE")
                elif tipo == "hueco_estudio":
                    # RF-10: el momento oportuno para una pregunta de la
                    # certificacion NVIDIA. El contenido del estudio es de
                    # otra ola (cerebro-jornada); aqui solo se marca el hueco.
                    log.info("superpower: hueco de estudio abierto")
                elif tipo == "ausente":
                    log.info("superpower: sin señales, AUSENTE (silencio total)")
            if MOTOR.estado == FOCO and estado_antes != FOCO:
                # Transicion real a FOCO (no cada tick mientras se sigue
                # dentro) -- registra_entrada_foco() alimenta RF-18.
                try:
                    historial_foco.registra_entrada_foco()
                except Exception as e:
                    log.warning("vigila_superpower: no se pudo registrar entrada a FOCO: %s", e)
        except Exception as e:
            log.warning("vigila_superpower: %s", e)
        await asyncio.sleep(30)


_SEMAFORO_ACENTO = {"normal": "cyan", "ambar": "amber", "rojo": "blood"}

# Ola agenda, Fase 2: se avisa una vez por "episodio" de desactualizacion,
# no en cada ciclo de vigila_ritmo() (10 s) -- eso seria un beep cada 10 s
# mientras el Atajo no corra. Se rearma solo cuando llega un POST nuevo
# (horas_desde_ultimo_post() vuelve a bajar del umbral).
_agenda_avisada_vieja = False


def _ocultar_calendarios_agenda() -> set:
    """RF-7 (Fase 2): que calendarios excluir del plan/vista, segun
    ajustes.yaml:agenda. Ambos en false por defecto (nada oculto)."""
    cfg = ajustes_actuales().get("agenda") or {}
    ocultar = set()
    if cfg.get("ocultar_trabajo"):
        ocultar.add("trabajo")
    if cfg.get("ocultar_personal"):
        ocultar.add("personal")
    return ocultar


def _aplica_cfg_ritmo():
    """Aplica servidor/ajustes.yaml:ritmo a RITMO -- editable en caliente,
    sin reiniciar el bridge (mismo patron que 'alertas'). Se llama en cada
    ciclo de vigila_ritmo() porque ajustes_actuales() ya cachea por mtime,
    asi que releerlo es barato."""
    cfg = (ajustes_actuales().get("ritmo") or {})
    modos_cfg = cfg.get("modos") or {}
    for nombre, over in modos_cfg.items():
        if nombre in RITMO._modos and isinstance(over, dict):
            RITMO._modos[nombre].update({k: v for k, v in over.items() if v is not None})
    if "minutos_ancla_bloquea_extension" in cfg:
        RITMO.minutos_ancla_bloquea_extension = int(cfg["minutos_ancla_bloquea_extension"])
    if "dias_via_fria" in cfg:
        RITMO.dias_via_fria = int(cfg["dias_via_fria"])


def _pinta_vista_ritmo(snap: dict) -> list:
    """RF-7 'vista inteligente': AHORA / barra de agotamiento / minutos /
    LUEGO / bandeja, dentro de las filas de hud_mostrar existente. Cero
    firmware nuevo (Fase 1): son filas de texto sobre el protocolo v2."""
    activo = snap.get("activo")
    filas = []
    if activo is None:
        if snap.get("esperando_confirmacion"):
            sig = snap.get("siguiente")
            filas.append(f"LISTO: {sig['titulo'][:18]}" if sig else "LISTO")
            filas.append("boton = empezar")
        else:
            filas.append("sin bloque activo")
    else:
        restante_min = activo["restante_seg"] // 60
        filas.append(f"AHORA: {activo['titulo'][:18]}")
        ancho = 10
        llenas = max(0, min(ancho, round(activo["pct_restante"] * ancho)))
        filas.append("#" * llenas + "-" * (ancho - llenas))
        filas.append(f"quedan {restante_min} min")
    sig = snap.get("siguiente")
    if sig and not snap.get("esperando_confirmacion"):
        filas.append(f"LUEGO: {sig['titulo'][:18]}")
    frias = snap.get("vias_frias") or []
    if frias:
        filas.append(f"vias frias: {len(frias)}")
    return filas


async def vigila_ritmo():
    """El pulso de Ritmo (ola ritmo, Fase 1): traduce lo que decide
    nucleo/ritmo.py::RITMO.tick() a HUD + tono, cada 10 s, para todo
    dispositivo vivo que quiera el servicio 'ritmo' (nucleo/canal.py).

    10 s y no 30 como vigila_superpower(): aqui hay countdown visible en
    pantalla (RF-7), 30 s se notaria a ojo como un salto.
    """
    while True:
        try:
            _aplica_cfg_ritmo()
            destinos = [c for c in REGISTRO.vivos() if c.quiere("ritmo")]
            for accion in RITMO.tick(estado_foco=MOTOR.estado):
                tipo = accion.get("tipo")
                if tipo == "aviso_cierre":
                    for c in destinos:
                        try:
                            await c.notifica(
                                f"cierra en {accion['minutos_restantes']} min", "warn", beep=True)
                        except Exception as e:
                            log.warning("ritmo: aviso_cierre a '%s': %s", c.device_id, e)
                elif tipo == "ofrece_extender":
                    for c in destinos:
                        try:
                            await c.notifica(
                                f"¿+{accion['minutos']} min? boton = si", "info", beep=True)
                        except Exception as e:
                            log.warning("ritmo: ofrece_extender a '%s': %s", c.device_id, e)
                elif tipo == "fin_bloque":
                    for c in destinos:
                        try:
                            await c.notifica("bloque cerrado", "info", beep=True)
                        except Exception as e:
                            log.warning("ritmo: fin_bloque a '%s': %s", c.device_id, e)
                elif tipo == "ancla_proxima":
                    log.info("ritmo: reunion anclada proxima, no se ofrece extension")
            snap = RITMO.snapshot()
            acento = "cyan"
            if snap.get("activo"):
                acento = _SEMAFORO_ACENTO.get(snap["activo"].get("semaforo"), "cyan")
            filas = _pinta_vista_ritmo(snap)
            semaforo = (snap.get("activo") or {}).get("semaforo", "normal")
            guardadas = MOTOR.guardadas()
            for c in destinos:
                try:
                    await c.mostrar("ritmo", "RITMO", filas, acento=acento)
                except Exception as e:
                    log.warning("ritmo: no se pudo pintar en '%s': %s", c.device_id, e)
                # Ademas del 'vista' anidado de arriba (que scifi_hud.cc sabe
                # pintar con color/badge por fila cuando ese componente este
                # enlazado), se difunden las MISMAS lineas como mensajes
                # planos "ritmo_linea"/"ritmo_reset"/"ritmo_acento" -- el
                # patron que ya usa difunde_noticias() para SEÑALES. Es lo
                # que el bring-up del Stick (protocolo_v2.c, sin parser JSON
                # anidado) sabe leer hoy (15/sep/2026, Fase A+B del plan
                # acordado con Mario). Un dispositivo que ya entiende el
                # 'vista' anidado simplemente ignora estos tipos desconocidos
                # (mismo "solo se loguea" de siempre) -- no hay regresion.
                try:
                    await envia_a_canal(c, "ritmo_reset", "")
                    for fila in filas:
                        await envia_a_canal(c, "ritmo_linea", fila)
                    await envia_a_canal(c, "ritmo_acento", semaforo)
                except Exception as e:
                    log.warning("ritmo: no se pudo difundir plano a '%s': %s", c.device_id, e)
                # Vista dedicada de bandeja (Fase 2, tasks.md: "cierra el
                # pendiente que dejó abierto superpower/tasks.md"): cuantos
                # avisos esperan un hueco (RF-5 de superpower), aparte de la
                # cuenta que ya aparecia mezclada en foco_estado(). Se pinta
                # SIEMPRE (aunque sea 0) para que la pantalla no desaparezca
                # justo cuando se vacia.
                try:
                    await c.mostrar("bandeja", "BANDEJA",
                                     [f"{guardadas} guardada{'s' if guardadas != 1 else ''}"],
                                     acento=("amber" if guardadas else "grey"))
                except Exception as e:
                    log.warning("bandeja: no se pudo pintar en '%s': %s", c.device_id, e)
            await _avisa_agenda_desactualizada(destinos)
        except Exception as e:
            log.warning("vigila_ritmo: %s", e)
        await asyncio.sleep(10)


async def _avisa_agenda_desactualizada(destinos):
    """Ola agenda, Fase 2 (RF-6: 'el HUD indica que la agenda esta
    desactualizada; no inventa reuniones ni se queda esperando'). Un solo
    aviso por episodio -- ver _agenda_avisada_vieja."""
    global _agenda_avisada_vieja
    from nucleo import agenda as _agenda
    try:
        horas = await asyncio.to_thread(_agenda.horas_desde_ultimo_post)
    except Exception as e:
        log.warning("agenda: no se pudo leer frescura: %s", e)
        return
    if horas is None:
        return   # nunca se recibio nada: RF-6 no es "desactualizada", es "nunca hubo"
    umbral = float((ajustes_actuales().get("agenda") or {}).get("horas_frescura", 24))
    if horas > umbral and not _agenda_avisada_vieja:
        _agenda_avisada_vieja = True
        for c in destinos:
            try:
                await c.notifica(f"agenda desactualizada ({horas:.0f}h)", "warn")
            except Exception as e:
                log.warning("agenda: no se pudo avisar frescura a '%s': %s", c.device_id, e)
    elif horas <= umbral:
        _agenda_avisada_vieja = False   # se rearma solo con un POST nuevo


def _aprende_correcciones_modo(bloques: list) -> None:
    """RF-17: si el panel (o cualquier cliente de ritmo_plan) manda un
    bloque de origen 'pendiente' con un modo distinto del que la heuristica
    pura le pondria (sin overrides -- asi se detecta una correccion real,
    no un eco del propio override ya aplicado), se recuerda esa correccion
    en preferencias_modo.json para que pese mas la proxima vez.

    Nunca tumba el guardado del plan: un fallo aqui se registra y se sigue
    (ver el try/except al llamar). No se llama para bloques de origen
    'agenda' u otro -- esos no pasan por _infiere_modo() en primer lugar.
    """
    for b in bloques or []:
        if not isinstance(b, dict) or b.get("origen") != "pendiente":
            continue
        titulo = str(b.get("titulo", "")).strip()
        modo = str(b.get("modo", "")).strip()
        if not titulo or not modo:
            continue
        propuesto = _infiere_modo(titulo)   # sin overrides: la heuristica pelada
        if modo != propuesto:
            try:
                preferencias_modo.guarda(titulo, modo)
            except Exception as e:
                log.warning("preferencias_modo: no se pudo guardar correccion: %s", e)


_DETAIL_DOC = {
    0: "3-5 pasos grandes, para quien solo necesita un mapa general",
    1: "6-10 pasos medianos, cada uno una accion concreta de unos minutos",
    2: "12-20 microsteps, cada uno tan pequeño que empezarlo no de miedo",
}


async def _desglosa_tarea(tarea: str, detail: int = 1) -> dict:
    """RF-19 (docs/investigacion/estado-del-arte-tdah-2026-actualizacion.md
    §2.3 y §5.3): la version propia del 'Magic ToDo' de Goblin Tools --
    la funcion mas valorada de todo el panorama 2026 segun las tres fuentes
    leidas para esa investigacion, y la unica pieza que a Goblin Tools le
    falta (login, memoria, agenda) es exactamente lo que RITMO+pendientes.py
    ya tienen. 'detail' (0-2) controla la granularidad, mismo concepto que
    el slider "spiciness" del original -- renombrado a 'detail' (15/sep,
    pedido explicito de Mario: "picante" sonaba raro en el panel).

    No usa RITMO ni nucleo/ritmo.py -- es una utilidad de LLM aislada, sin
    estado, para no arriesgar nada del motor ya probado. Vive aqui (no en
    nucleo/) porque necesita 'cadenas', que solo existe en este proceso.
    """
    tarea = (tarea or "").strip()
    if not tarea:
        return {"error": "falta 'tarea'"}
    detail = max(0, min(2, int(detail)))
    if not cadenas.get("llm") or not cadenas["llm"].miembros:
        return {"error": "sin proveedor de llm disponible ahora mismo"}
    prompt = (
        "Desglosa la siguiente tarea, que a alguien con TDAH le cuesta empezar, "
        f"en {_DETAIL_DOC[detail]}. Cada paso debe ser una accion concreta que "
        "se pueda hacer de inmediato, en imperativo, sin explicaciones. "
        "Responde SOLO con un array JSON de strings, sin texto alrededor, "
        f"sin numerar (el orden ya lo da el array).\n\nTarea: {tarea}"
    )
    try:
        respuesta = await cadenas["llm"].chat([{"role": "user", "content": prompt}])
    except Exception as e:
        log.warning("ritmo_desglosa: fallo el llm: %s", e)
        return {"error": f"no se pudo desglosar: {e}"}
    pasos = _extrae_lista_json(respuesta)
    if not pasos:
        return {"error": "el llm no devolvio una lista utilizable", "crudo": respuesta[:200]}
    return {"tarea": tarea, "detail": detail, "pasos": pasos[:20]}


def _extrae_lista_json(texto: str) -> list[str]:
    """El LLM a veces envuelve el JSON en ```json ... ``` o le añade una
    frase antes/despues pese a la instruccion -- se extrae el primer
    array balanceado en vez de asumir que la respuesta es JSON puro."""
    inicio = texto.find("[")
    fin = texto.rfind("]")
    if inicio == -1 or fin == -1 or fin < inicio:
        return []
    try:
        datos = json.loads(texto[inicio:fin + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(datos, list):
        return []
    return [str(p).strip() for p in datos if str(p).strip()]


async def envia_raw(ws, dato):
    """Escribe en el socket serializando con el resto de productores de ESE
    dispositivo.

    Resuelve el Canal dueño de `ws` en el registro y pasa por su
    Canal.send() (con SU lock). Si `ws` no es ningun dispositivo registrado
    -- el caso del canal de control, que tiene un unico productor -- escribe
    directo. Escribir directo al socket de un dispositivo es lo que
    corrompia el stream cuando la telemetria caia en mitad del envio del
    audio -- ver el comentario en Canal.send().
    """
    canal = REGISTRO.por_socket(ws)
    if canal is not None:
        await canal.send(dato)
    else:
        await ws.send(dato)      # canal de control: un solo productor


async def envia(ws, tipo, valor):
    await envia_raw(ws, json.dumps({"t": tipo, "v": valor}))


# ============================================================
#  Ritmo de envio del audio
# ============================================================
# EL PROBLEMA QUE RESUELVE (era la causa de los cortes):
#
# Antes esto era un simple bucle que volcaba la frase entera en el socket tan
# rapido como el TCP la aceptara. Los numeros no cuadran ni de lejos:
#
#     frase de 3 s a 16 kHz 16-bit .... 96 000 bytes
#     colchon del ESP32 (AUDIO_MS_BUF)  19 200 bytes
#
# Y en voice.c el volcado al colchon es xStreamBufferSend(..., 0): timeout
# CERO, o sea que lo que no cabe SE TIRA. De ahi el aviso que ya salia en el
# monitor: "audio: se descarto un trozo". Eso era el corte, y lo unico que
# lo mantenia a raya era la ventana TCP del ESP32 -- es decir, por accidente,
# no por diseno. En cuanto el WiFi iba fino, se perdia media frase.
#
# COMO SE ARREGLA: el que conoce la duracion del audio es el productor, asi
# que la regulacion va aqui. Es un reloj virtual: se lleva la cuenta de
# cuantos segundos de audio se han mandado y se compara con los segundos de
# reloj transcurridos. Se permite ir por delante COLCHON_S como mucho.
#
# Por que un reloj virtual y no un sleep fijo por trozo: si la red se atasca
# un momento, el adelanto se consume solo y NO se duerme -- se recupera el
# ritmo sin acumular retraso. Un sleep fijo sumaria el atasco a la espera y
# el audio se iria quedando atras hasta vaciar el colchon.
#
# Xiaozhi no necesita nada de esto porque manda Opus: la misma frase de 3 s
# son 6 kB y cabe entera en cualquier colchon. La compresion ES su control de
# flujo. Mientras aqui se mande PCM, este regulador hace ese papel.
#
# Ola 1: el regulador recibe el CANAL, no un ws suelto -- toda escritura pasa
# por Canal.send() y su lock. Un Ritmo vive mas alla de una sola frase a
# proposito (ver la clase): si se reiniciara en cada una, el colchon
# acumulado se perderia y entre frase y frase habria un hueco audible justo
# cuando MENOS sobra tiempo.
TROZO_AUDIO = 2048                       # 1024 muestras = 64 ms a 16 kHz
COLCHON_S   = 0.40                       # adelanto maximo permitido
BYTES_POR_S = SAMPLE_RATE * 2            # 16-bit mono


class Ritmo:
    """Regulador de una respuesta completa (todas sus frases), para UN
    canal. Con dos dispositivos hablando a la vez, cada uno usa su propio
    Ritmo: un dispositivo lento no arrastra al otro (ver voz-sticks3, R5.4)."""

    def __init__(self):
        self._t0 = None
        self._audio_s = 0.0

    async def envia(self, canal, audio: bytes):
        loop = asyncio.get_running_loop()
        if self._t0 is None:
            self._t0 = loop.time()
        for k in range(0, len(audio), TROZO_AUDIO):
            trozo = audio[k:k + TROZO_AUDIO]
            try:
                await canal.send(trozo)
            except RuntimeError:
                return   # el dispositivo se desconecto a mitad de la respuesta
            self._audio_s += len(trozo) / BYTES_POR_S
            adelanto = self._audio_s - (loop.time() - self._t0)
            if adelanto > COLCHON_S:
                await asyncio.sleep(adelanto - COLCHON_S)


import yaml as _yaml
from pathlib import Path as _Path

# BUG REAL (encontrado 23/ago/2026): esta funcion leia agente.config.data
# ["ajustes"], es decir una seccion dentro de config.yaml. Pero el panel web
# (panel_api.py, AJUSTES_PATH) escribe los toggles en un fichero DISTINTO,
# servidor/ajustes.yaml -- nunca se cruzan. Confirmar tts_leer_respuestas:
# true en ajustes.yaml no significaba nada para debe_hablar(): siempre
# devolvia el default True de config.yaml (vacio ahi), asi que en teoria
# deberia haber funcionado iaual -- pero el archivo real, revisado en el
# servidor, tenia YAML corrupto (lineas pegadas tipo "cyanefectos: true" y
# "trueorden: 5"), lo que puede tumbar el parseo silenciosamente segun donde
# yaml.safe_load() decida fallar. Fix: leer directo el fichero que usa el
# panel, con manejo de error explicito en vez de tragarselo.
_AJUSTES_PATH = _Path(__file__).resolve().parent / "ajustes.yaml"
_ajustes_mtime = 0.0
_ajustes_cache = {}


def ajustes_actuales() -> dict:
    """Ajustes guardados por el panel web (servidor/ajustes.yaml).

    Releido solo si el fichero cambio en disco (mismo patron que
    Config.recarga_si_cambio). Si el YAML esta roto se loguea el error UNA
    vez (no en bucle) y se devuelve el ultimo valor bueno conocido, para no
    dejar el asistente mudo por un typo en el panel.
    """
    global _ajustes_mtime, _ajustes_cache
    try:
        m = _AJUSTES_PATH.stat().st_mtime
    except OSError:
        return _ajustes_cache
    if m <= _ajustes_mtime:
        return _ajustes_cache
    try:
        datos = _yaml.safe_load(_AJUSTES_PATH.read_text()) or {}
        _ajustes_cache = datos
        _ajustes_mtime = m
    except Exception as e:
        log.error("ajustes.yaml no se pudo leer (YAML invalido?): %s", e)
        _ajustes_mtime = m   # no reintentar en bucle sobre el mismo fichero roto
    return _ajustes_cache


def debe_hablar() -> bool:
    """Toggle 'Leer respuestas del agente en voz alta' del panel."""
    aj = ajustes_actuales()
    return bool(aj.get("tts_leer_respuestas", True))


def pantalla_activa(id_pantalla: str) -> bool:
    """¿Esa pantalla del carrusel esta encendida en el panel?

    Ante la duda (sin ajustes guardados todavia) se asume que si: el
    comportamiento por defecto tiene que ser el de siempre.
    """
    for p in (ajustes_actuales().get("pantallas") or []):
        if isinstance(p, dict) and p.get("id") == id_pantalla:
            return bool(p.get("activa", True))
    return True


async def atiende_control(ws):
    """Cliente de rol 'control': el MCP 'dispositivo'.

    Traduce {"t":"cmd","fn":...} a llamadas sobre un Canal del registro
    (seleccionado por args.device_id) y devuelve {"t":"res"}. Separado de
    atiende() porque un cliente de control no manda audio ni necesita los
    feeds periodicos, y porque nunca se registra en REGISTRO: es el caso que
    envia_raw() distingue para escribir sin pasar por un Canal.
    """
    log.info("cliente de control conectado desde %s", ws.remote_address)
    # Un cliente de control recibe capacidades acotadas y caducas. Por defecto
    # todas menos nada: el minimo se afina por cliente cuando haga falta.
    sujeto = f"control:{ws.remote_address[1]}"
    GUARDIA.concede(sujeto, segundos=3600)
    try:
        async for msg in ws:
            if isinstance(msg, bytes):
                continue
            d = json.loads(msg)
            if d.get("t") != "cmd":
                continue
            rid, fn, args = d.get("rid"), d.get("fn"), d.get("args") or {}
            target_canal = REGISTRO.obtener(args.get("device_id"))
            try:
                if fn == "estado_todos":
                    v = REGISTRO.snapshot_todos()
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue

                # Ola 8 (operacion): salud del proceso para /api/salud del
                # panel. Antes de exigir target_canal vivo, como
                # 'estado_todos': no es sobre UN dispositivo.
                if fn == "salud":
                    v = {
                        "uptime_seg": round(time.time() - _inicio, 1),
                        "memoria_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                        "dispositivos_vivos": len(REGISTRO.vivos()),
                        "dispositivos_total": len(REGISTRO.snapshot_todos()),
                        "audio": dict(_contadores),
                    }
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue

                # Ola 8, "Consumo de proveedores por dispositivo en el
                # panel": desglose por device_id (no solo el total de
                # 'salud') + que proveedor esta activo ahora mismo en cada
                # cadena. No es atribucion turno-a-turno (Cadena.chat/
                # transcribir/sintetizar no reportan quien respondio, y
                # tocar ese camino caliente para esto no vale el riesgo) --
                # es "cuanto uso este dispositivo" + "con que proveedor
                # trabajaria ahora", que es lo que RF de la tarea pide.
                if fn == "consumo":
                    v = {
                        "por_dispositivo": dict(_contadores_por_dispositivo),
                        "proveedores_activos": {
                            cap: (cadena.activo.nombre if cadena.activo else None)
                            for cap, cadena in cadenas.items()
                        },
                    }
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue

                # Los comandos de SuperPower operan sobre MOTOR (estado de
                # UNA persona, ver nucleo/foco.py), no sobre un Canal de UN
                # dispositivo -- por eso van antes de resolver target_canal
                # y de exigirle estar vivo. mcps/foco.py es quien los manda.
                if fn == "foco_estado":
                    v = MOTOR.snapshot()
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue
                if fn == "foco_descanso":
                    minutos = args.get("minutos")
                    pendientes = MOTOR.descanso(duracion=minutos * 60 if minutos else None)
                    await _entrega_bandeja(pendientes)
                    v = {"estado": MOTOR.estado, "entregadas": len(pendientes),
                         "avisos": [a.texto for a in pendientes]}
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue
                if fn == "foco_reanuda":
                    v = {"estado": MOTOR.estado, "migas": MOTOR.reanuda()}
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue
                if fn == "foco_anota":
                    MOTOR.anota(args.get("nota", ""))
                    v = {"ok": True}
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue

                # Ola ritmo: igual que foco_*, RITMO es el estado de UNA
                # persona, no de un dispositivo -- va antes de target_canal.
                # mcps/ritmo.py es quien manda estos comandos.
                if fn == "ritmo_estado":
                    v = RITMO.snapshot()
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue
                if fn == "ritmo_empieza":
                    v = RITMO.empieza(bloque_id=args.get("bloque_id"), titulo=args.get("titulo"),
                                       modo=args.get("modo"), minutos=args.get("minutos"),
                                       via=args.get("via"))
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue
                if fn == "ritmo_extiende":
                    minutos = args.get("minutos")
                    v = RITMO.extiende(minutos=minutos) if minutos else RITMO.extiende()
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue
                if fn == "ritmo_salta":
                    v = RITMO.salta()
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue
                if fn == "ritmo_cierra":
                    v = RITMO.cierra()
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue
                if fn == "ritmo_confirma":
                    v = RITMO.confirma()
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue
                if fn == "ritmo_plan":
                    bloques = args.get("bloques")
                    if bloques is not None:
                        try:
                            _aprende_correcciones_modo(bloques)
                        except Exception as e:
                            log.warning("ritmo_plan: fallo aprendiendo correcciones de modo: %s", e)
                        RITMO.plan_set(bloques)
                    v = {"plan": RITMO.plan()}
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue
                if fn == "ritmo_vias":
                    v = {"vias_frias": RITMO.vias_frias()}
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue
                if fn == "ritmo_desglosa":
                    v = await _desglosa_tarea(args.get("tarea", ""), args.get("detail", 1))
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue
                # "resumen_feeds": pedido explicito del usuario, historial
                # de chat/noticias/mac para el panel -- son cachés globales
                # (_historial_chat/_cache_noticias/_cache_mac), no de un
                # dispositivo concreto, asi que va con el mismo patron que
                # los "foco_*" de arriba: antes de exigir un target_canal vivo.
                if fn == "resumen_feeds":
                    v = {"chat": _historial_chat[-20:], "noticias": _cache_noticias,
                         "mac": _cache_mac}
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue
                if not target_canal.vivo and fn != "estado":
                    v = {"error": f"no hay ESP32 conectado al puente (dispositivo: {target_canal.device_id})"}
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue

                # Frontera de confianza: valida, acota y sanea ANTES de que
                # nada salga hacia el dispositivo. Lo que devuelve la guardia
                # es lo unico que se usa; los args originales se descartan.
                args = GUARDIA.revisa(sujeto, fn, args, target_canal.limites["ancho"])

                if args.get("__dry_run__"):
                    v = {"ok": True, "dry_run": True, "validado": args}
                elif fn == "pregunta":
                    v = await target_canal.pregunta(args["txt"], args["opciones"],
                                             args["timeout"])
                elif fn == "pregunta_async":
                    v = await target_canal.pregunta_async(args["txt"], args["opciones"],
                                                   args["timeout"])
                elif fn == "consulta":
                    v = target_canal.consulta(args["qid"])
                elif fn == "mostrar":
                    v = await target_canal.mostrar(args["id"], args["titulo"],
                                            args["filas"], args["acento"],
                                            args["orden"], args["ttl"])
                elif fn == "borrar":
                    v = await target_canal.borrar(args["id"])
                elif fn == "notifica":
                    v = await target_canal.notifica(args["txt"], args["nivel"],
                                             args["beep"])
                elif fn == "hablar":
                    texto = args["texto"]
                    audio = await asyncio.to_thread(sintetiza, texto)
                    # Mismo regulador que la respuesta de voz: este camino
                    # (una herramienta MCP pidiendo hablar) tenia el mismo
                    # volcado sin ritmo y por tanto los mismos cortes.
                    await Ritmo().envia(target_canal, audio)
                    await envia_a_canal(target_canal, "texto", texto[:40].upper())
                    v = f"Dicho en voz alta: {texto}"
                elif fn == "estado":
                    v = target_canal.snapshot()
                elif fn == "configurar":
                    v = await target_canal.configurar(args.get("brillo"), args.get("volumen"),
                                               args.get("tema_hud"), args.get("efectos"))
                elif fn == "pantallas":
                    v = await target_canal.pantallas(args.get("activas"), args.get("orden"))
                elif fn == "wifi":
                    v = await target_canal.wifi(args.get("accion", "guardar"),
                                         args.get("ssid", ""),
                                         args.get("password", ""))
                elif fn == "reiniciar":
                    v = await target_canal.reiniciar()
                else:
                    v = {"error": f"comando desconocido '{fn}'"}
            except Rechazo as e:
                # Rechazo es esperado, no un fallo: se devuelve al modelo con
                # explicacion para que corrija en vez de reintentar a ciegas.
                log.warning("RECHAZADO %s de %s: %s", fn, sujeto, e)
                v = {"error": str(e), "rechazado_por": "guardia"}
            except Exception as e:
                log.error("cmd %s fallo: %s", fn, e)
                v = {"error": str(e)}
            await ws.send(json.dumps({"t": "res", "rid": rid, "v": v},
                                     ensure_ascii=False))
    except websockets.ConnectionClosed:
        log.info("cliente de control desconectado")
    finally:
        GUARDIA.revoca(sujeto)


def _token_valido(recibido: str) -> bool:
    """Compara contra HUD_TOKEN con tiempo constante (hmac.compare_digest):
    una comparacion con '==' normal filtra por temporizacion cuanto del
    token es correcto, byte a byte -- exactamente lo que un atacante en la
    misma red necesita para adivinarlo por fuerza bruta con paciencia.

    HUD_TOKEN vacio (no configurado en .env) => modo abierto, el de hoy.
    Es una mejora honesta sobre texto plano (design.md ola 2), no una
    solucion: el salto real es wss:// + nginx, que llega en la ola 8/9.
    """
    esperado = os.getenv("HUD_TOKEN", "")
    if not esperado:
        return True
    return hmac.compare_digest(str(recibido or ""), esperado)


async def atiende(ws):
    """Atiende una conexion de DISPOSITIVO (no de control).

    Sin variable global: `canal_disp` se resuelve una vez al principio y es
    la unica referencia que usa toda la funcion. Es la garantia de que el
    audio, el texto y el estado de ESTA conexion van siempre al Canal de
    ESTE dispositivo, nunca al de otro.
    """
    from nucleo.canal import DEVICE_ID_POR_DEFECTO
    dev_id = DEVICE_ID_POR_DEFECTO
    dev_type = "bola"
    d_saludo = None

    try:
        primero = await asyncio.wait_for(ws.recv(), timeout=2.0)
        if isinstance(primero, str):
            d = json.loads(primero)
            if d.get("t") == "hola" and d.get("rol") == "control":
                return await atiende_control(ws)
            if d.get("t") == "hola":
                if not _token_valido(d.get("token")):
                    log.warning("token invalido de %s, se cierra la conexion",
                                ws.remote_address)
                    await ws.close(4401, "token")
                    return
                d_saludo = d
                # Identidad explicita (device_id/id) y, desde la ola 2,
                # geometria/entrada/servicios -- ver Canal.saluda(). Nada de
                # adivinar el tipo de placa por el string de firmware: esa
                # rama por tipo es justo lo que la ley 5 (un firmware, dos
                # placas via limites/geometria) prohibe.
                dev_id = d.get("device_id") or d.get("id") or dev_id
                dev_type = d.get("device_type") or d.get("tipo") or dev_type
    except (asyncio.TimeoutError, json.JSONDecodeError, websockets.ConnectionClosed):
        primero = None       # firmware v1: no saluda, se asume la bola

    canal_disp = REGISTRO.obtener_o_crear(dev_id, dev_type)
    if d_saludo:
        canal_disp.saluda(d_saludo)

    log.info("ESP32 conectado ('%s', tipo=%s) desde %s",
             canal_disp.device_id, canal_disp.device_type, ws.remote_address)
    canal_disp.conecta(ws)
    buffer = bytearray()
    await envia(ws, "estado", "idle")
    # Empuja el ultimo lote de cada feed que este dispositivo quiera: no
    # tiene que esperar al proximo ciclo de difunde_noticias/telemetria
    # (hasta 15 min) para ver algo en pantalla.
    await empuja_estado_actual(canal_disp)
    await _asegura_plan_del_dia(canal_disp)

    try:
        async for msg in ws:
            if isinstance(msg, bytes):
                buffer.extend(msg)
                continue

            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                log.warning("mensaje no-JSON del ESP32, se descarta: %r", msg[:80])
                continue
            if data.get("t") == "ping":
                continue

            # Respuesta a un hud_preguntar: desbloquea al agente que espera
            if data.get("t") == "respuesta":
                canal_disp.resuelve(data.get("qid", ""), data.get("opcion", -1))
                continue

            # Estado real de la placa: brillo, volumen, tema, bateria, heap...
            # Lo manda el firmware al conectar y cada pocos segundos. Es lo que
            # permite que el panel muestre lo que el aparato TIENE, y no solo
            # lo ultimo que el panel le mando -- si el usuario cambia el
            # brillo en la pantalla de AJUSTES, el panel se entera igual.
            # Se actualiza el estado de ESTE canal, nunca el de otro.
            if data.get("t") == "estado_disp":
                canal_disp.estado.update({k: v for k, v in data.items() if k != "t"})
                continue

            # Una vista interactiva: el usuario toco una fila
            if data.get("t") == "evento":
                # Ola 2: se guarda con el device_id de ESTE canal, no solo se
                # loguea -- asi una tool MCP (mcps/dispositivo.py) puede
                # responder "se toco en el Stick" en vez de perderlo en texto.
                ev = canal_disp.registra_evento(data.get("id"), data.get("fila"))
                log.info("evento en vista '%s': fila %s (dispositivo: %s)",
                         ev["id"], ev["fila"], ev["device_id"])
                continue

            if data.get("t") == "fin":
                if len(buffer) < SAMPLE_RATE:        # menos de 0.5 s: ruido
                    buffer.clear()
                    _cuenta(canal_disp, "turnos_ruido")
                    await envia(ws, "estado", "idle")
                    continue

                # TODO el procesamiento va dentro de un try. Antes, si fallaba
                # cualquier cosa -- el STT, el agente, una herramienta MCP, el
                # TTS -- la excepcion subia hasta el bucle, salia del 'async
                # for' y tumbaba la CONEXION del ESP32. Un error puntual del
                # LLM dejaba la placa desconectada hasta que alguien la
                # reiniciaba, y en el panel se veia como "SIN DISPOSITIVO".
                # Un fallo procesando una frase no puede costar el enlace: se
                # avisa en pantalla y se sigue escuchando.
                try:
                    # Un turno de voz es la señal de actividad mas fuerte que
                    # existe: alguien esta, sin duda, delante del aparato.
                    # SuperPower la usa para salir de AUSENTE y para medir
                    # cuanto lleva "en" la conversacion como app.
                    MOTOR.latido(f"voz:{canal_disp.device_id}")
                    await envia(ws, "estado", "processing")
                    wav = pcm_a_wav(bytes(buffer))
                    buffer.clear()

                    # DEBUG TEMPORAL: guarda una copia del WAV que de verdad
                    # se manda al STT, para poder escucharlo y confirmar si
                    # es voz limpia, ruido, o silencio con picos -- sin esto
                    # solo se puede especular a partir del numero de pico.
                    # Quitar este bloque una vez confirmado el problema real.
                    try:
                        import shutil
                        shutil.copy(wav, "/home/ubuntu/ultimo_turno_debug.wav")
                        log.info("copia de depuracion guardada en /home/ubuntu/ultimo_turno_debug.wav")
                    except Exception as e:
                        log.warning("no se pudo guardar la copia de depuracion: %s", e)

                    texto = await asyncio.to_thread(transcribe, wav)
                    try:
                        os.unlink(wav)
                    except OSError:
                        pass
                    log.info("escuchado: %s", texto)
                    if not texto:
                        await envia(ws, "texto", "NO TE ENTENDI")
                        await envia(ws, "estado", "idle")
                        continue

                    await envia(ws, "texto", texto[:40].upper())
                    await envia(ws, "tu", texto[:33].upper())
                    _chat_cachea("tu", texto[:33].upper())
                    respuesta = await agente.chat(texto)
                    log.info("respuesta: %s", respuesta)

                    await envia(ws, "texto", respuesta[:40].upper())
                    for trozo in _en_lineas(respuesta, 33)[:3]:
                        await envia(ws, "ia", trozo)
                        _chat_cachea("ia", trozo)

                    # DEBUG TEMPORAL: log explicito del valor real de
                    # debe_hablar() y del contenido crudo que esta leyendo,
                    # para dejar de adivinar por que el bloque de sintesis
                    # no deja rastro en el log. Quitar una vez confirmado.
                    _dh = debe_hablar()
                    log.info("debe_hablar()=%s  ajustes_actuales()=%s  path=%s",
                              _dh, ajustes_actuales(), _AJUSTES_PATH)

                    # Con la lectura por voz apagada en el panel, la respuesta
                    # se muestra en pantalla y ya: ni se sintetiza (no se gasta
                    # cuota de TTS) ni se pone el HUD en "speaking", que seria
                    # mentira.
                    _cuenta(canal_disp, "turnos_ok")
                    if debe_hablar():
                        await envia(ws, "estado", "speaking")
                        # Frase a frase, no todo de golpe. Antes se generaba el
                        # audio COMPLETO y solo entonces empezaba a sonar: con
                        # una respuesta de tres frases eran varios segundos de
                        # silencio con el HUD ya en "speaking". Ahora la
                        # primera frase suena mientras se sintetiza la
                        # siguiente, y la espera percibida baja a la de una
                        # sola frase.
                        frases = _en_frases(respuesta)
                        siguiente = asyncio.create_task(
                            asyncio.to_thread(sintetiza, frases[0]))
                        # Un unico Ritmo para toda la respuesta, ligado a ESTE
                        # canal: ver la clase Ritmo.
                        ritmo = Ritmo()
                        for i, _ in enumerate(frases):
                            audio = await siguiente
                            # Se lanza ya la sintesis de la proxima, para que
                            # se prepare mientras esta se esta reproduciendo.
                            if i + 1 < len(frases):
                                siguiente = asyncio.create_task(
                                    asyncio.to_thread(sintetiza, frases[i + 1]))
                            await ritmo.envia(canal_disp, audio)
                    await envia(ws, "estado", "idle")

                except websockets.ConnectionClosed:
                    raise            # esta si es el enlace: que la maneje el for
                except Exception as e:
                    # Cualquier otro fallo es de ESTA frase, no del enlace.
                    log.exception("fallo procesando la peticion: %s", e)
                    _cuenta(canal_disp, "turnos_error")
                    buffer.clear()
                    try:
                        await envia(ws, "texto", "ERROR, REINTENTA")
                        await envia(ws, "estado", "idle")
                    except websockets.ConnectionClosed:
                        raise

    except websockets.ConnectionClosed:
        log.info("ESP32 desconectado")
    except Exception as e:
        # Que un fallo inesperado no deje el proceso en un estado raro: se
        # registra con traza y se limpia igual en el finally. El ESP32
        # reconecta solo a los 2 s.
        log.exception("error inesperado atendiendo al ESP32: %s", e)
    finally:
        # Solo se suelta el canal si sigue siendo NUESTRA conexion. Si la
        # placa se reconecto mientras esta corrutina agonizaba, el canal ya
        # apunta a la sesion nueva y borrarlo aqui la dejaria muerta: el
        # panel diria "SIN DISPOSITIVO" con el aparato perfectamente
        # conectado. (Regresion cubierta por test_multidispositivo.py.)
        if canal_disp.ws is ws:
            canal_disp.desconecta()


def ip_local() -> str:
    """IP de esta maquina en la red local (sin depender de interfaces concretas)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))       # no envia nada, solo elige la ruta
        return s.getsockname()[0]
    finally:
        s.close()


def anuncia_mdns(ip: str):
    """Publica _hud._tcp para que el ESP32 no necesite una IP fija."""
    try:
        from zeroconf import ServiceInfo, Zeroconf
        info = ServiceInfo(
            "_hud._tcp.local.",
            "mario-hud._hud._tcp.local.",
            addresses=[socket.inet_aton(ip)],
            port=PORT,
            properties={"version": "1"},
            server="mario-hud.local.",
        )
        zc = Zeroconf()
        zc.register_service(info)
        log.info("anunciado como mario-hud.local (%s)", ip)
        return zc
    except ImportError:
        log.warning("sin zeroconf (pip install zeroconf): usa la IP %s a mano", ip)
    except Exception as e:
        log.warning("mDNS no disponible: %s", e)
    return None


async def main():
    await arranca_agente()
    ip = ip_local()
    anuncia_mdns(ip)
    log.info("IP de este equipo: %s", ip)
    log.info("escuchando en ws://%s:%d", HOST, PORT)

    # Servicios periodicos: una tarea de proceso cada uno, no una por
    # conexion. Se reparten solos a REGISTRO.vivos() en cada ciclo.
    asyncio.create_task(difunde_noticias())
    asyncio.create_task(difunde_clima())
    asyncio.create_task(difunde_telemetria())
    asyncio.create_task(vigila_alertas())
    asyncio.create_task(vigila_superpower())
    asyncio.create_task(vigila_ritmo())

    # ping_interval/ping_timeout: el servidor tambien vigila el enlace. Sin
    # esto, un ESP32 que desaparece de malas maneras (se va el WiFi, se corta
    # la luz) deja aqui una conexion abierta para siempre: su Canal sigue
    # creyendo que hay dispositivo, el panel intenta aplicar ajustes contra
    # un socket muerto, y cuando la placa vuelve de verdad hay dos sesiones.
    # Con esto el servidor detecta el silencio en ~40 s y limpia.
    async with websockets.serve(atiende, HOST, PORT, max_size=None,
                                ping_interval=20, ping_timeout=20,
                                close_timeout=5):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
