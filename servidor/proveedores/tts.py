"""Texto -> voz. Devuelve siempre PCM 16-bit mono crudo, listo para el I2S."""
import audioop, io, json, os, shutil, subprocess, sys, tempfile, wave, httpx
from pathlib import Path
from .base import ProveedorTTS, ErrorProveedor
from .llm import _expande


class TTSMacOS(ProveedorTTS):
    """La voz del sistema. Cero dependencias, cero coste, funciona sin red."""
    nombre = "macos"

    def __init__(self, nombre="macos", cfg=None):
        cfg = cfg or {}
        self.nombre = nombre
        self.voz = cfg.get("voice", "Monica")

    def disponible(self) -> bool:
        return os.path.exists("/usr/bin/say")

    def sintetizar(self, texto, sample_rate=16000) -> bytes:
        aiff = tempfile.NamedTemporaryFile(suffix=".aiff", delete=False).name
        raw = tempfile.NamedTemporaryFile(suffix=".raw", delete=False).name
        try:
            subprocess.run(["say", "-v", self.voz, "-o", aiff, texto],
                           check=True, capture_output=True)
            subprocess.run(["afconvert", "-f", "caff", "-d", f"LEI16@{sample_rate}",
                            "-c", "1", aiff, raw], check=True, capture_output=True)
            with open(raw, "rb") as f:
                return f.read()[4096:]        # salta la cabecera CAF
        except Exception as e:
            raise ErrorProveedor(f"{self.nombre}: {e}") from e
        finally:
            for p in (aiff, raw):
                try: os.unlink(p)
                except OSError: pass


class TTSPiper(ProveedorTTS):
    """Neuronal, local y multiplataforma. Respaldo de TTS que SI funciona en
    un servidor Linux (a diferencia de 'macos', que exige el comando 'say').
    Sin API, sin clave, sin limite de uso -- corre en la propia VM.
    """
    nombre = "piper"

    def __init__(self, nombre="piper", cfg=None):
        cfg = cfg or {}
        self.nombre = nombre
        self.modelo = self._resuelve_modelo(cfg.get("model_path", ""))
        # Piper genera el audio a la frecuencia nativa del modelo de voz
        # (el sidecar <modelo>.onnx.json trae "audio":{"sample_rate":N}) --
        # los modelos 'x_low' suelen ser 16000 Hz, no los 24000 Hz que pide
        # el firmware. Si no se reconvierte, el audio suena mas rapido/agudo
        # de lo debido (mismo problema que se valida en TTSOpenAICompatibleWav
        # para Groq, aqui hay que resolverlo en vez de solo detectarlo porque
        # Piper no tiene otra frecuencia que ofrecer).
        self.tasa_modelo = self._lee_tasa_modelo()

    @staticmethod
    def _resuelve_modelo(ruta: str) -> str:
        """La voz configurada, o cualquier otra voz espanola de esa carpeta.

        Sin esto, un fallo al descargar el .onnx deja el TTS entero fuera de
        juego y la cadena cae a Groq, que lee espanol con fonetica inglesa. Es
        preferible una voz espanola peor que una inglesa: se prefiere siempre
        la configurada, y solo si no esta se busca una alternativa es_*.
        """
        if not ruta or os.path.exists(ruta):
            return ruta
        carpeta = Path(ruta).parent
        if not carpeta.is_dir():
            return ruta
        # 'medium' y 'high' antes que 'low'/'x_low': mejor calidad primero.
        def prioridad(p: Path):
            n = p.name
            return (0 if "high" in n else 1 if "medium" in n else
                    2 if "x_low" not in n else 3, n)
        candidatas = sorted(carpeta.glob("es_*.onnx"), key=prioridad)
        return str(candidatas[0]) if candidatas else ruta

    def _lee_tasa_modelo(self) -> int:
        ruta_json = f"{self.modelo}.json"
        try:
            with open(ruta_json) as f:
                return json.load(f)["audio"]["sample_rate"]
        except Exception:
            return 22050  # frecuencia por defecto mas comun en voces Piper

    def _comando(self) -> list | None:
        """Como invocar a Piper en ESTA maquina, o None si no esta.

        Se busca en tres sitios porque el binario no siempre acaba en el PATH
        del servicio: systemd arranca con un PATH minimo, asi que un
        'pip install piper-tts' dentro del venv deja el ejecutable en
        venv/bin/piper pero 'piper' a secas no se encuentra. Eso daba un fallo
        confuso -- la cadena caia al siguiente proveedor (Groq, en ingles) sin
        que quedara claro por que, aunque el modelo de voz estuviera bien.
        """
        exe = shutil.which("piper")
        if exe:
            return [exe]
        # Mismo bin que el interprete que nos ejecuta: el venv del servicio.
        junto = Path(sys.executable).with_name("piper")
        if junto.exists():
            return [str(junto)]
        # Instalado como paquete pero sin script: se invoca como modulo.
        try:
            import piper  # noqa: F401
            return [sys.executable, "-m", "piper"]
        except ImportError:
            return None

    def disponible(self) -> bool:
        # Antes solo se miraba el modelo. Con el .onnx presente pero sin el
        # binario, esto decia "disponible" y luego fallaba al sintetizar.
        if not (self.modelo and os.path.exists(self.modelo)):
            return False
        return self._comando() is not None

    def sintetizar(self, texto, sample_rate=16000) -> bytes:
        cmd = self._comando()
        if cmd is None:
            raise ErrorProveedor(
                f"{self.nombre}: no se encontro el ejecutable. "
                f"Instalalo con:  ./venv/bin/pip install piper-tts")
        try:
            p = subprocess.run(cmd + ["--model", self.modelo, "--output_raw"],
                               input=texto.encode(), capture_output=True, check=True)
            pcm = p.stdout
        except subprocess.CalledProcessError as e:
            # stderr trae el motivo real (modelo corrupto, voz incompatible);
            # sin esto solo se veia "returned non-zero exit status 1".
            err = (e.stderr or b"").decode(errors="replace").strip()[:300]
            raise ErrorProveedor(f"{self.nombre}: {err or e}") from e
        except Exception as e:
            raise ErrorProveedor(f"{self.nombre}: {e}") from e
        if not pcm:
            raise ErrorProveedor(f"{self.nombre}: no devolvio audio")
        if self.tasa_modelo != sample_rate:
            # audioop.ratecv: resampleo lineal, de la libreria estandar, sin
            # dependencias nuevas. Sobra para voz (no es audio de alta
            # fidelidad) y evita que el HUD reproduzca la respuesta a la
            # velocidad/tono incorrectos.
            pcm, _ = audioop.ratecv(pcm, 2, 1, self.tasa_modelo, sample_rate, None)
        return pcm


class TTSPolly(ProveedorTTS):
    """AWS Polly -- voz neuronal en espanol, pagada con el credito de la
    cuenta (mismas credenciales AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY que
    usa Bedrock).

    OJO: el formato OutputFormat="pcm" de Polly SOLO acepta 8000 u 16000 Hz
    de SampleRate -- pedirle 24000 (lo que espera el firmware) con pcm no es
    una combinacion valida. Sin este aviso, se pedia 24000 directo y el
    audio llegaba corrupto (sonaba a ruido/estatica, no a voz). Se pide
    siempre a 16000 Hz (soportado) y se resamplea con audioop, igual que ya
    hace TTSPiper con las voces que no nacen a 24000 Hz.
    """
    nombre = "polly"
    _TASA_POLLY = 16000     # unica tasa "grande" que Polly acepta con pcm

    def __init__(self, nombre="polly", cfg=None):
        cfg = cfg or {}
        self.nombre = nombre
        self.voz = cfg.get("voice", "Lucia")     # Lucia = es-ES neuronal
        self.engine = cfg.get("engine", "neural")
        self.region = cfg.get("region") or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        self._cliente = None

    def disponible(self) -> bool:
        try:
            import boto3  # noqa: F401
        except ImportError:
            return False
        return bool(os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"))

    def _boto(self):
        if self._cliente is None:
            import boto3
            self._cliente = boto3.client("polly", region_name=self.region)
        return self._cliente

    def sintetizar(self, texto, sample_rate=16000) -> bytes:
        try:
            cliente = self._boto()
            resp = cliente.synthesize_speech(
                Text=texto,
                OutputFormat="pcm",
                VoiceId=self.voz,
                Engine=self.engine,
                SampleRate=str(self._TASA_POLLY),
                LanguageCode="es-ES",
            )
            pcm = resp["AudioStream"].read()
        except Exception as e:
            detalle = str(e) or type(e).__name__
            raise ErrorProveedor(f"{self.nombre}: {detalle}") from e
        if not pcm:
            raise ErrorProveedor(f"{self.nombre}: no devolvio audio")
        if self._TASA_POLLY != sample_rate:
            pcm, _ = audioop.ratecv(pcm, 2, 1, self._TASA_POLLY, sample_rate, None)
        return pcm


class TTSOpenAICompatible(ProveedorTTS):
    """OpenAI, Azure OpenAI y equivalentes con /audio/speech que aceptan
    response_format=pcm directamente (el caso comun: devuelven PCM 16-bit
    mono crudo al sample_rate pedido, listo para el I2S sin tocar nada)."""
    def __init__(self, nombre, cfg):
        self.nombre = nombre
        self.base_url = cfg["base_url"].rstrip("/")
        self.api_key = _expande(cfg.get("api_key", ""))
        self.model = cfg.get("model", "tts-1")
        self.voz = cfg.get("voice", "alloy")
        # Casi todos aceptan "pcm"; algunos (Groq) solo devuelven "wav" y
        # por eso es configurable en vez de fijo, ver TTSOpenAICompatibleWav.
        self.formato = cfg.get("response_format", "pcm")

    def disponible(self) -> bool:
        return bool(self.api_key)

    def sintetizar(self, texto, sample_rate=16000) -> bytes:
        try:
            r = httpx.post(f"{self.base_url}/audio/speech",
                           headers={"Authorization": f"Bearer {self.api_key}"},
                           json={"model": self.model, "voice": self.voz,
                                 "input": texto, "response_format": self.formato},
                           timeout=60)
            if r.status_code >= 400:
                # raise_for_status() sola solo dice "400 Bad Request" -- el
                # cuerpo trae la razon real (voz invalida, texto vacio, etc.)
                raise ErrorProveedor(f"{self.nombre}: {r.status_code} {r.text}")
            return r.content          # PCM 16-bit mono al sample_rate pedido
        except ErrorProveedor:
            raise
        except Exception as e:
            raise ErrorProveedor(f"{self.nombre}: {e}") from e


class TTSOpenAICompatibleWav(TTSOpenAICompatible):
    """Como TTSOpenAICompatible, pero para APIs (Groq/Orpheus a fecha de
    escribir esto) que solo devuelven WAV, nunca PCM crudo.

    Se lee la cabecera WAV de verdad (con el modulo 'wave', no un offset de
    44 bytes a ciegas: no todos los WAV tienen exactamente ese tamano de
    cabecera) para sacar el PCM y, sobre todo, para comprobar que la
    frecuencia de muestreo es la que el firmware espera. El I2S del ESP32
    esta fijo a 24 kHz (ver SAMPLE_RATE en websocket_bridge.py): si el
    proveedor devolviera otra frecuencia y se mandara tal cual, el audio
    sonaria mas agudo/grave y mas rapido/lento, no directamente roto. Mejor
    fallar con un error claro (la cadena degrada sola al siguiente TTS) que
    reproducir algo que suena mal sin explicar por que.
    """
    def __init__(self, nombre, cfg):
        cfg = {**cfg, "response_format": "wav"}
        super().__init__(nombre, cfg)

    def sintetizar(self, texto, sample_rate=16000) -> bytes:
        wav_bytes = super().sintetizar(texto, sample_rate)
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as w:
                canales, ancho, tasa, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
                pcm = w.readframes(n)
        except Exception as e:
            raise ErrorProveedor(f"{self.nombre}: WAV invalido: {e}") from e
        if ancho != 2 or canales != 1:
            raise ErrorProveedor(
                f"{self.nombre}: audio {canales} canal(es) / {ancho*8} bits, "
                f"se esperaba mono 16-bit")
        if tasa != sample_rate:
            raise ErrorProveedor(
                f"{self.nombre}: devolvio {tasa} Hz, el firmware espera {sample_rate} Hz. "
                f"Reproducirlo tal cual sonaria mal (velocidad/tono); no se manda.")
        return pcm


REGISTRO_TTS = {
    "macos": TTSMacOS,
    "piper": TTSPiper,
    "polly": TTSPolly,
    "openai-compatible": TTSOpenAICompatible,
    "openai-compatible-wav": TTSOpenAICompatibleWav,
}
