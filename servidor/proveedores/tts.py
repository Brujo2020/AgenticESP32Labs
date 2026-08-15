"""Texto -> voz. Devuelve siempre PCM 16-bit mono crudo, listo para el I2S."""
import os, subprocess, tempfile, httpx
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
    """OpenAI, Azure OpenAI, Groq y equivalentes: /audio/speech."""
    def __init__(self, nombre, cfg):
        self.nombre = nombre
        self.base_url = cfg["base_url"].rstrip("/")
        self.api_key = _expande(cfg.get("api_key", ""))
        self.model = cfg.get("model", "tts-1")
        self.voz = cfg.get("voice", "alloy")

    def disponible(self) -> bool:
        return bool(self.api_key)

    def sintetizar(self, texto, sample_rate=24000) -> bytes:
        try:
            r = httpx.post(f"{self.base_url}/audio/speech",
                           headers={"Authorization": f"Bearer {self.api_key}"},
                           json={"model": self.model, "voice": self.voz,
                                 "input": texto, "response_format": "pcm"},
                           timeout=60)
            r.raise_for_status()
            return r.content          # PCM 16-bit mono 24 kHz
        except Exception as e:
            raise ErrorProveedor(f"{self.nombre}: {e}") from e


REGISTRO_TTS = {
    "macos": TTSMacOS,
    "piper": TTSPiper,
    "openai-compatible": TTSOpenAICompatible,
}
