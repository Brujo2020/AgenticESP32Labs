"""Voz -> texto. Local primero (privado, sin coste), nube como respaldo."""
import os, time, uuid, httpx
from .base import ProveedorSTT, ErrorProveedor
from .llm import _expande


class STTTranscribe(ProveedorSTT):
    """Amazon Transcribe, modo batch (no streaming): mismo credito/cuenta AWS
    que Bedrock y Polly, sin depender de una libreria nueva (amazon-transcribe
    de streaming exige un SDK aparte, con su propio manejo de eventos async).

    El flujo es el mismo patron que usan sistemas tipo Xiaozhi para el STT en
    la nube -- no streaming en vivo, sino "grabo el turno completo, lo subo,
    pido la transcripcion, la leo": sencillo, sin estado, y con el WAV que ya
    genera pcm_a_wav() no hace falta tocar nada del pipeline de audio.

        1. sube el WAV a un bucket S3 (se crea solo si no existe)
        2. lanza un StartTranscriptionJob sobre ese objeto
        3. hace poll a start_transcription_job hasta COMPLETED/FAILED
        4. descarga el JSON de resultado y saca el texto

    Nombre de bucket fijo y predecible (no aleatorio): asi un job que se cae
    a mitad dejar basura reconocible, facil de limpiar a mano si hace falta.
    """
    nombre = "transcribe"

    def __init__(self, nombre="transcribe", cfg=None):
        cfg = cfg or {}
        self.nombre = nombre
        self.region = cfg.get("region") or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        self._s3 = None
        self._transcribe = None
        self._bucket = None

    def disponible(self) -> bool:
        try:
            import boto3  # noqa: F401
        except ImportError:
            return False
        return bool(os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"))

    def _clientes(self):
        if self._s3 is None:
            import boto3
            self._s3 = boto3.client("s3", region_name=self.region)
            self._transcribe = boto3.client("transcribe", region_name=self.region)
        return self._s3, self._transcribe

    def _bucket_listo(self, s3) -> str:
        if self._bucket:
            return self._bucket
        import boto3
        # El nombre de bucket S3 es GLOBAL (entre todas las cuentas de AWS),
        # asi que no puede ser fijo a secas -- se cuelga del account id, que
        # es unico por cuenta y estable entre arranques del servidor.
        cuenta = boto3.client("sts", region_name=self.region).get_caller_identity()["Account"]
        nombre = f"agentic-esp32-stt-{cuenta}"
        try:
            if self.region == "us-east-1":
                s3.create_bucket(Bucket=nombre)
            else:
                s3.create_bucket(Bucket=nombre, CreateBucketConfiguration={
                    "LocationConstraint": self.region})
        except s3.exceptions.BucketAlreadyOwnedByYou:
            pass
        except Exception as e:
            # BucketAlreadyExists (de OTRA cuenta) no deberia poder pasar con
            # el sufijo del account id, pero si pasara, mejor fallar claro.
            if "BucketAlreadyExists" in str(e) and "BucketAlreadyOwnedByYou" not in str(e):
                raise ErrorProveedor(
                    f"{self.nombre}: el bucket '{nombre}' ya existe en OTRA cuenta AWS "
                    f"(nombre S3 global en conflicto)") from e
        self._bucket = nombre
        return nombre

    def transcribir(self, wav_path, idioma="es") -> str:
        try:
            s3, tr = self._clientes()
            bucket = self._bucket_listo(s3)
            clave = f"turno-{uuid.uuid4().hex}.wav"
            s3.upload_file(wav_path, bucket, clave)

            codigo_idioma = "es-ES" if idioma.startswith("es") else idioma
            nombre_job = f"agentic-{uuid.uuid4().hex}"
            tr.start_transcription_job(
                TranscriptionJobName=nombre_job,
                LanguageCode=codigo_idioma,
                MediaFormat="wav",
                Media={"MediaFileUri": f"s3://{bucket}/{clave}"},
                OutputBucketName=bucket,
            )

            # Poll corto: los turnos son de pocos segundos de audio, Transcribe
            # batch suele resolver en 3-8 s. 30 intentos x 1 s = 30 s de tope
            # antes de rendirse y dejar que la cadena caiga al siguiente STT.
            estado = None
            for _ in range(30):
                r = tr.get_transcription_job(TranscriptionJobName=nombre_job)
                estado = r["TranscriptionJob"]["TranscriptionJobStatus"]
                if estado in ("COMPLETED", "FAILED"):
                    break
                time.sleep(1)

            if estado != "COMPLETED":
                razon = (r["TranscriptionJob"].get("FailureReason", "")
                         if estado == "FAILED" else "tiempo agotado esperando el job")
                raise ErrorProveedor(f"{self.nombre}: {razon or estado}")

            uri = r["TranscriptionJob"]["Transcript"]["TranscriptFileUri"]
            resp = httpx.get(uri, timeout=30)
            resp.raise_for_status()
            datos = resp.json()
            texto = datos["results"]["transcripts"][0]["transcript"].strip()

            # Limpieza: el objeto de audio y el resultado no hacen falta mas.
            # Best-effort -- si falla el borrado no se rompe la transcripcion.
            try:
                s3.delete_object(Bucket=bucket, Key=clave)
                s3.delete_object(Bucket=bucket, Key=f"{nombre_job}.json")
                tr.delete_transcription_job(TranscriptionJobName=nombre_job)
            except Exception:
                pass

            return texto
        except ErrorProveedor:
            raise
        except Exception as e:
            raise ErrorProveedor(f"{self.nombre}: {e}") from e


class STTWhisperMLX(ProveedorSTT):
    """Local en Apple Silicon. Sin coste, sin salir de la maquina."""
    nombre = "mlx-whisper"

    def __init__(self, nombre="mlx-whisper", cfg=None):
        cfg = cfg or {}
        self.nombre = nombre
        self.repo = cfg.get("model", "mlx-community/whisper-small-mlx")

    def disponible(self) -> bool:
        try:
            import mlx_whisper  # noqa
            return True
        except ImportError:
            return False

    def transcribir(self, wav_path, idioma="es") -> str:
        try:
            import mlx_whisper
            r = mlx_whisper.transcribe(wav_path, path_or_hf_repo=self.repo, language=idioma)
            return (r.get("text") or "").strip()
        except Exception as e:
            raise ErrorProveedor(f"{self.nombre}: {e}") from e


class STTGroq(ProveedorSTT):
    """Whisper large v3 en Groq. Gratis con limites y muy rapido."""
    nombre = "groq"

    def __init__(self, nombre="groq", cfg=None):
        cfg = cfg or {}
        self.nombre = nombre
        self.api_key = _expande(cfg.get("api_key", "")) or os.getenv("GROQ_API_KEY", "")
        self.model = cfg.get("model", "whisper-large-v3-turbo")

    def disponible(self) -> bool:
        return bool(self.api_key)

    # Contexto para Whisper. Con audio corto y algo de ruido, el modelo tiende
    # a "rellenar": devuelve frases hechas de sus datos de entrenamiento, y en
    # ingles con sorprendente frecuencia. Darle una pista del dominio y del
    # idioma reduce mucho ese invento -- y es justo el fallo que hacia parecer
    # que el agente contestaba cosas sin relacion con lo que se le pedia.
    PISTA = ("Conversacion en espanol con un asistente de voz domestico. "
             "Se le pregunta por el clima, la hora, noticias, el ordenador, "
             "y se le dan ordenes cortas.")

    def transcribir(self, wav_path, idioma="es") -> str:
        try:
            with open(wav_path, "rb") as f:
                r = httpx.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files={"file": ("audio.wav", f, "audio/wav")},
                    data={"model": self.model, "language": idioma,
                          "prompt": self.PISTA,
                          # 0 = deterministico. Por defecto Whisper puede
                          # muestrear y, con audio dudoso, eso es exactamente
                          # cuando se inventa cosas.
                          "temperature": 0},
                    timeout=60,
                )
            r.raise_for_status()
            return (r.json().get("text") or "").strip()
        except Exception as e:
            raise ErrorProveedor(f"{self.nombre}: {e}") from e


class STTOpenAICompatible(ProveedorSTT):
    """Cualquier endpoint con /audio/transcriptions: OpenAI, Azure, local."""
    def __init__(self, nombre, cfg):
        self.nombre = nombre
        self.base_url = cfg["base_url"].rstrip("/")
        self.api_key = _expande(cfg.get("api_key", ""))
        self.model = cfg.get("model", "whisper-1")

    def disponible(self) -> bool:
        return bool(self.api_key) or "localhost" in self.base_url

    def transcribir(self, wav_path, idioma="es") -> str:
        try:
            with open(wav_path, "rb") as f:
                r = httpx.post(f"{self.base_url}/audio/transcriptions",
                               headers={"Authorization": f"Bearer {self.api_key}"},
                               files={"file": ("audio.wav", f, "audio/wav")},
                               data={"model": self.model, "language": idioma},
                               timeout=60)
            r.raise_for_status()
            return (r.json().get("text") or "").strip()
        except Exception as e:
            raise ErrorProveedor(f"{self.nombre}: {e}") from e


REGISTRO_STT = {
    "transcribe": STTTranscribe,
    "mlx-whisper": STTWhisperMLX,
    "groq": STTGroq,
    "openai-compatible": STTOpenAICompatible,
}
