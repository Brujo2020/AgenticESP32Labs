"""
Contratos de proveedor. Todo lo que entra o sale del asistente pasa por
una de estas tres interfaces, de modo que el hyperscaler es intercambiable
sin tocar ni el firmware ni la logica del agente.
"""
from abc import ABC, abstractmethod
from typing import Any


class Proveedor(ABC):
    nombre: str = "?"
    capacidad: str = "?"          # "llm" | "stt" | "tts"

    def disponible(self) -> bool:
        """True si tiene credenciales y dependencias para operar."""
        return True


class ProveedorLLM(Proveedor):
    capacidad = "llm"

    @abstractmethod
    async def chat(self, mensajes: list[dict], herramientas: list | None = None) -> str: ...


class ProveedorSTT(Proveedor):
    capacidad = "stt"

    @abstractmethod
    def transcribir(self, wav_path: str, idioma: str = "es") -> str: ...


class ProveedorTTS(Proveedor):
    capacidad = "tts"

    @abstractmethod
    def sintetizar(self, texto: str, sample_rate: int = 16000) -> bytes: ...


class ErrorProveedor(RuntimeError):
    """Fallo recuperable: la cadena debe intentar el siguiente proveedor."""
