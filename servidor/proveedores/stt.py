"""Voz -> texto. Local primero (privado, sin coste), nube como respaldo."""
import os, httpx
from .base import ProveedorSTT, ErrorProveedor
from .llm import _expande


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
    "openai-compatible": STTOpenAICompatible,
}
