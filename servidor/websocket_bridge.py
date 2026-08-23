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
"""
import asyncio, audioop, json, os, socket, subprocess, tempfile, wave, logging
import websockets

from nucleo.entorno import carga_env
carga_env()   # servidor/.env, si existe — ver panel.py. No pisa el entorno real.

from nucleo import Agente, Config, MCPPool
from nucleo.canal import CANAL
from nucleo.guardia import GUARDIA, Rechazo
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


async def envia_noticias(ws):
    """Refresca titulares al conectar y luego cada 15 minutos.

    Si la pantalla de noticias esta apagada en el panel, ni se piden los RSS:
    no tiene sentido gastar red y CPU en algo que el usuario no puede ver.
    """
    while True:
        try:
            if not pantalla_activa("noticias"):
                await asyncio.sleep(60)
                continue
            ts = await titulares(5)
            if ts:
                await envia(ws, "noticias_reset", "")
                for t in ts:
                    await envia(ws, "noticia", t)
                log.info("enviados %d titulares", len(ts))
        except Exception as e:
            log.warning("noticias: %s", e)
        await asyncio.sleep(15 * 60)


async def envia_telemetria(ws):
    """Estado del Mac y de las apps creativas, cada 5 s.

    Cada feed se salta si su pantalla esta apagada en el panel: son consultas
    al sistema cada 5 segundos, no vale la pena hacerlas a ciegas.
    """
    while True:
        try:
            if pantalla_activa("mac"):
                mac = await asyncio.to_thread(lineas_mac)
                await envia(ws, "mac_reset", "")
                for l in mac:
                    await envia(ws, "mac", l)

            if pantalla_activa("creativo"):
                cre = await asyncio.to_thread(lineas_creativo)
                await envia(ws, "creativo_reset", "")
                for l in cre:
                    await envia(ws, "creativo", l)
        except Exception as e:
            log.warning("telemetria: %s", e)
        await asyncio.sleep(5)


async def envia_raw(ws, dato):
    """Escribe en el socket serializando con el resto de productores.

    Si el destino es el ESP32, se pasa por CANAL.send(), que tiene el lock.
    Escribir directo aqui es lo que corrompia el stream cuando la telemetria
    caia en mitad del envio del audio -- ver el comentario en Canal.send().
    """
    if ws is CANAL.ws:
        await CANAL.send(dato)
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
TROZO_AUDIO = 2048                       # 1024 muestras = 64 ms a 16 kHz
COLCHON_S   = 0.40                       # adelanto maximo permitido
BYTES_POR_S = SAMPLE_RATE * 2            # 16-bit mono


class Ritmo:
    """Regulador de una respuesta completa (todas sus frases).

    Vive mas alla de una sola frase a proposito: si se reiniciara en cada una,
    el colchon acumulado se perderia y entre frase y frase habria un hueco
    audible justo cuando MENOS sobra tiempo (el TTS de la siguiente ya viene
    con su propia latencia).
    """

    def __init__(self):
        self._t0 = None
        self._audio_s = 0.0

    async def envia(self, ws, audio: bytes):
        loop = asyncio.get_running_loop()
        if self._t0 is None:
            self._t0 = loop.time()
        for k in range(0, len(audio), TROZO_AUDIO):
            trozo = audio[k:k + TROZO_AUDIO]
            await envia_raw(ws, trozo)
            self._audio_s += len(trozo) / BYTES_POR_S
            adelanto = self._audio_s - (loop.time() - self._t0)
            if adelanto > COLCHON_S:
                await asyncio.sleep(adelanto - COLCHON_S)


def ajustes_actuales() -> dict:
    """Bloque 'ajustes' de config.yaml, releido si el panel lo cambio.

    El panel web corre en otro proceso y escribe el mismo fichero; sin
    releer, un toggle del panel no se notaria hasta reiniciar el bridge.
    """
    if agente and agente.config:
        agente.config.recarga_si_cambio()
        return (agente.config.data or {}).get("ajustes") or {}
    return {}


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

    Traduce {"t":"cmd","fn":...} a llamadas sobre CANAL y devuelve {"t":"res"}.
    Separado de atiende() porque un cliente de control no manda audio ni
    necesita los feeds periodicos.
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
            try:
                if not CANAL.vivo and fn != "estado":
                    v = {"error": "no hay ESP32 conectado al puente"}
                    await ws.send(json.dumps({"t": "res", "rid": rid, "v": v}))
                    continue

                # Frontera de confianza: valida, acota y sanea ANTES de que
                # nada salga hacia el dispositivo. Lo que devuelve la guardia
                # es lo unico que se usa; los args originales se descartan.
                args = GUARDIA.revisa(sujeto, fn, args, CANAL.limites["ancho"])

                if args.get("__dry_run__"):
                    v = {"ok": True, "dry_run": True, "validado": args}
                elif fn == "pregunta":
                    v = await CANAL.pregunta(args["txt"], args["opciones"],
                                             args["timeout"])
                elif fn == "pregunta_async":
                    v = await CANAL.pregunta_async(args["txt"], args["opciones"],
                                                   args["timeout"])
                elif fn == "consulta":
                    v = CANAL.consulta(args["qid"])
                elif fn == "mostrar":
                    v = await CANAL.mostrar(args["id"], args["titulo"],
                                            args["filas"], args["acento"],
                                            args["orden"], args["ttl"])
                elif fn == "borrar":
                    v = await CANAL.borrar(args["id"])
                elif fn == "notifica":
                    v = await CANAL.notifica(args["txt"], args["nivel"],
                                             args["beep"])
                elif fn == "hablar":
                    texto = args["texto"]
                    audio = await asyncio.to_thread(sintetiza, texto)
                    # Mismo regulador que la respuesta de voz: este camino
                    # (una herramienta MCP pidiendo hablar) tenia el mismo
                    # volcado sin ritmo y por tanto los mismos cortes.
                    await Ritmo().envia(CANAL.ws, audio)
                    await envia(CANAL.ws, "texto", texto[:40].upper())
                    v = f"Dicho en voz alta: {texto}"
                elif fn == "estado":
                    v = CANAL.snapshot()
                elif fn == "configurar":
                    v = await CANAL.configurar(args.get("brillo"), args.get("volumen"),
                                               args.get("tema_hud"), args.get("efectos"))
                elif fn == "pantallas":
                    v = await CANAL.pantallas(args.get("activas"), args.get("orden"))
                elif fn == "wifi":
                    v = await CANAL.wifi(args.get("accion", "guardar"),
                                         args.get("ssid", ""),
                                         args.get("password", ""))
                elif fn == "reiniciar":
                    v = await CANAL.reiniciar()
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


async def atiende(ws):
    # El primer mensaje decide el rol: un cliente de control no es un ESP32.
    # El firmware v1 no saluda, asi que se agota el plazo y se asume dispositivo.
    # Dos segundos de espera solo en la conexion inicial, no por mensaje.
    try:
        primero = await asyncio.wait_for(ws.recv(), timeout=2.0)
        if isinstance(primero, str):
            d = json.loads(primero)
            if d.get("t") == "hola" and d.get("rol") == "control":
                return await atiende_control(ws)
            if d.get("t") == "hola":
                CANAL.saluda(d)
    except (asyncio.TimeoutError, json.JSONDecodeError, websockets.ConnectionClosed):
        primero = None       # firmware v1: no saluda, se asume dispositivo

    log.info("ESP32 conectado desde %s", ws.remote_address)
    CANAL.conecta(ws)
    buffer = bytearray()
    await envia(ws, "estado", "idle")
    tarea_news = asyncio.create_task(envia_noticias(ws))
    tarea_tele = asyncio.create_task(envia_telemetria(ws))

    try:
        async for msg in ws:
            if isinstance(msg, bytes):
                buffer.extend(msg)
                continue

            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                # Un mensaje mal formado (truncado, cortado a mitad de un
                # frame, un firmware con un bug como el de voice_talk_stop
                # mandando un JSON incompleto) no puede tirar toda la
                # conexion: se descarta ese mensaje y se sigue escuchando.
                log.warning("mensaje no-JSON del ESP32, se descarta: %r", msg[:80])
                continue
            if data.get("t") == "ping":
                continue

            # Respuesta a un hud_preguntar: desbloquea al agente que espera
            if data.get("t") == "respuesta":
                CANAL.resuelve(data.get("qid", ""), data.get("opcion", -1))
                continue

            # Estado real de la placa: brillo, volumen, tema, bateria, heap...
            # Lo manda el firmware al conectar y cada pocos segundos. Es lo que
            # permite que el panel muestre lo que el aparato TIENE, y no solo
            # lo ultimo que el panel le mando -- si el usuario cambia el brillo
            # en la pantalla de AJUSTES, el panel se entera igual.
            if data.get("t") == "estado_disp":
                CANAL.estado.update({k: v for k, v in data.items() if k != "t"})
                continue

            # Una vista interactiva: el usuario toco una fila
            if data.get("t") == "evento":
                log.info("evento en vista '%s': fila %s",
                         data.get("id"), data.get("fila"))
                continue

            if data.get("t") == "fin":
                if len(buffer) < SAMPLE_RATE:        # menos de 0.5 s: ruido
                    buffer.clear()
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
                    respuesta = await agente.chat(texto)
                    log.info("respuesta: %s", respuesta)

                    await envia(ws, "texto", respuesta[:40].upper())
                    for trozo in _en_lineas(respuesta, 33)[:3]:
                        await envia(ws, "ia", trozo)

                    # Con la lectura por voz apagada en el panel, la respuesta
                    # se muestra en pantalla y ya: ni se sintetiza (no se gasta
                    # cuota de TTS) ni se pone el HUD en "speaking", que seria
                    # mentira.
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
                        # Un unico Ritmo para toda la respuesta: ver su clase.
                        ritmo = Ritmo()
                        for i, _ in enumerate(frases):
                            audio = await siguiente
                            # Se lanza ya la sintesis de la proxima, para que
                            # se prepare mientras esta se esta reproduciendo.
                            if i + 1 < len(frases):
                                siguiente = asyncio.create_task(
                                    asyncio.to_thread(sintetiza, frases[i + 1]))
                            await ritmo.envia(ws, audio)
                    await envia(ws, "estado", "idle")

                except websockets.ConnectionClosed:
                    raise            # esta si es el enlace: que la maneje el for
                except Exception as e:
                    # Cualquier otro fallo es de ESTA frase, no del enlace.
                    log.exception("fallo procesando la peticion: %s", e)
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
        # Solo se suelta el CANAL si sigue siendo NUESTRA conexion. Si la placa
        # se reconecto mientras esta corrutina agonizaba, el CANAL ya apunta a
        # la sesion nueva y borrarlo aqui la dejaria muerta: el panel diria
        # "SIN DISPOSITIVO" con el aparato perfectamente conectado.
        if CANAL.ws is ws:
            CANAL.desconecta()
        tarea_news.cancel()
        tarea_tele.cancel()


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
    # ping_interval/ping_timeout: el servidor tambien vigila el enlace. Sin
    # esto, un ESP32 que desaparece de malas maneras (se va el WiFi, se corta
    # la luz) deja aqui una conexion abierta para siempre: CANAL sigue
    # creyendo que hay dispositivo, el panel intenta aplicar ajustes contra un
    # socket muerto, y cuando la placa vuelve de verdad hay dos sesiones.
    # Con esto el servidor detecta el silencio en ~40 s y limpia.
    async with websockets.serve(atiende, HOST, PORT, max_size=None,
                                ping_interval=20, ping_timeout=20,
                                close_timeout=5):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
