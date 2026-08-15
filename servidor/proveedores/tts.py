"""Texto -> voz. Devuelve siempre PCM 16-bit mono crudo, listo para el I2S."""
import io, os, subprocess, tempfile, wave, httpx
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

    def sintetizar(self, texto, sample_rate=24000) -> bytes:
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
    """Neuronal, local y multiplataforma. Para demos edge fuera de macOS."""
    nombre = "piper"

    def __init__(self, nombre="piper", cfg=None):
        cfg = cfg or {}
        self.nombre = nombre
        self.modelo = cfg.get("model_path", "")

    def disponible(self) -> bool:
        return bool(self.modelo) and os.path.exists(self.modelo)

    def sintetizar(self, texto, sample_rate=24000) -> bytes:
        try:
            p = subprocess.run(["piper", "--model", self.modelo, "--output_raw"],
                               input=texto.encode(), capture_output=True, check=True)
            return p.stdout
        except Exception as e:
            raise ErrorProveedor(f"{self.nombre}: {e}") from e


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

    def sintetizar(self, texto, sample_rate=24000) -> bytes:
        try:
            r = httpx.post(f"{self.base_url}/audio/speech",
                           headers={"Authorization": f"Bearer {self.api_key}"},
                           json={"model": self.model, "voice": self.voz,
                                 "input": texto, "response_format": self.formato},
                           timeout=60)
            r.raise_for_status()
            return r.content          # PCM 16-bit mono al sample_rate pedido
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

    def sintetizar(self, texto, sample_rate=24000) -> bytes:
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
    "openai-compatible": TTSOpenAICompatible,
    "openai-compatible-wav": TTSOpenAICompatibleWav,
}
