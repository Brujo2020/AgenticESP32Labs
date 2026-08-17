"""Voz -> texto. Local primero (privado, sin coste), nube como respaldo."""
import asyncio, os, threading, wave, httpx
from .base import ProveedorSTT, ErrorProveedor
from .llm import _expande


class _LoopDeFondo:
    """Un solo event loop de asyncio, persistente, en su propio hilo.

    STTTranscribe necesita correr codigo async (el SDK de Amazon Transcribe
    no tiene version sincrona), pero el resto del servidor (websocket_bridge,
    el pool de MCP) ya vive en SU PROPIO event loop principal. Crear y
    destruir un event loop nuevo por cada transcripcion (via asyncio.run()
    dentro del hilo que usa asyncio.to_thread) chocaba con los generadores
    async del pool de MCP -- un "RuntimeError: Attempted to exit cancel
    scope in a different task" intermitente que rompia el turno de voz a
    medias (por eso a veces no sonaba nada, o el texto salia corrupto: el
    turno se interrumpia en medio del proceso, no despues).

    Con un solo loop de fondo, reusado en cada llamada en vez de crear uno
    nuevo cada vez, se evita esa interferencia con el loop principal.
    """
    _loop = None
    _hilo = None
    _candado = threading.Lock()

    @classmethod
    def _obten_loop(cls):
        with cls._candado:
            if cls._loop is None:
                cls._loop = asyncio.new_event_loop()
                cls._hilo = threading.Thread(target=cls._loop.run_forever, daemon=True)
                cls._hilo.start()
            return cls._loop

    @classmethod
    def ejecuta(cls, coro):
        loop = cls._obten_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop).result()


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

    def transcribir(self, wav_path, idioma="es") -> str:
        try:
            with open(wav_path, "rb") as f:
                r = httpx.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files={"file": ("audio.wav", f, "audio/wav")},
                    data={"model": self.model, "language": idioma},
                    timeout=60,
                )
            r.raise_for_status()
            return (r.json().get("text") or "").strip()
        except Exception as e:
            raise ErrorProveedor(f"{self.nombre}: {e}") from e


class STTTranscribe(ProveedorSTT):
    """Amazon Transcribe (streaming) -- mismo credito AWS que Bedrock/Polly.

    A diferencia de Groq (un POST y listo), Transcribe streaming necesita una
    conexion bidireccional: se manda el audio ya grabado en trozos por un
    lado, y se van recibiendo eventos con el texto por el otro, hasta que se
    cierra el canal de audio. No es mas rapido que Groq (agrega el overhead
    de abrir el stream), pero corre en la misma nube que ya factura
    Bedrock/Polly y evita depender de la clave de Groq para STT.

    Requiere el paquete 'amazon-transcribe' (SDK aparte, no es boto3):
        pip install amazon-transcribe
    """
    nombre = "transcribe"
    _IDIOMAS = {"es": "es-ES", "en": "en-US"}

    def __init__(self, nombre="transcribe", cfg=None):
        cfg = cfg or {}
        self.nombre = nombre
        self.region = cfg.get("region") or os.getenv("AWS_DEFAULT_REGION", "us-east-1")

    def disponible(self) -> bool:
        try:
            import amazon_transcribe  # noqa: F401
        except ImportError:
            return False
        return bool(os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"))

    def transcribir(self, wav_path, idioma="es") -> str:
        # NO usar asyncio.run() aqui: crear/cerrar un event loop nuevo por
        # llamada chocaba con el pool de MCP del proceso principal (ver
        # _LoopDeFondo mas arriba). Se ejecuta en el loop persistente de
        # fondo en su lugar.
        try:
            return _LoopDeFondo.ejecuta(self._transcribir_async(wav_path, idioma))
        except Exception as e:
            detalle = str(e) or type(e).__name__
            raise ErrorProveedor(f"{self.nombre}: {detalle}") from e

    async def _transcribir_async(self, wav_path, idioma) -> str:
        from amazon_transcribe.client import TranscribeStreamingClient
        from amazon_transcribe.handlers import TranscriptResultStreamHandler

        with wave.open(wav_path, "rb") as w:
            tasa = w.getframerate()
            pcm = w.readframes(w.getnframes())

        cliente = TranscribeStreamingClient(region=self.region)
        stream = await cliente.start_stream_transcription(
            language_code=self._IDIOMAS.get(idioma, "es-ES"),
            media_sample_rate_hz=tasa,
            media_encoding="pcm",
        )

        texto_final = []

        class _Handler(TranscriptResultStreamHandler):
            async def handle_transcript_event(self, transcript_event):
                for resultado in transcript_event.transcript.results:
                    if not resultado.is_partial:
                        for alt in resultado.alternatives:
                            texto_final.append(alt.transcript)

        handler = _Handler(stream.output_stream)

        async def _manda_audio():
            trozo = 1024 * 4
            for i in range(0, len(pcm), trozo):
                await stream.input_stream.send_audio_event(audio_chunk=pcm[i:i + trozo])
            await stream.input_stream.end_stream()

        await asyncio.gather(_manda_audio(), handler.handle_events())
        return " ".join(texto_final).strip()


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
    "mlx-whisper": STTWhisperMLX,
    "groq": STTGroq,
    "transcribe": STTTranscribe,
    "openai-compatible": STTOpenAICompatible,
}
