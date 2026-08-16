import os
import yaml
from pathlib import Path
from typing import Any, Dict, Optional

class Config:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self._mtime = 0.0
        self.data = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config: {self.config_path}")
        self._mtime = self.config_path.stat().st_mtime
        with open(self.config_path, "r") as f:
            return yaml.safe_load(f) or {}

    def recarga_si_cambio(self) -> bool:
        """Relee config.yaml si el fichero cambio en disco.

        El panel web (panel_api.py) corre en OTRO proceso y escribe este mismo
        fichero. Sin esto, cambiar la ciudad del clima o un perfil de agente
        desde el panel no tenia ningun efecto hasta reiniciar el bridge de voz
        -- que es exactamente lo que el panel promete evitar.

        Si el YAML queda a medio escribir en el instante de la lectura, se
        conserva el anterior: mejor config vieja que un agente sin config.
        """
        try:
            m = self.config_path.stat().st_mtime
        except OSError:
            return False
        if m <= self._mtime:
            return False
        try:
            self.data = self._load_config()
            return True
        except Exception:
            self._mtime = m      # no reintentar en bucle sobre un fichero roto
            return False

    def get_model_config(self, model_name: str) -> Dict[str, Any]:
        models = self.data.get("llm_models", {})
        if model_name not in models:
            raise ValueError(f"Modelo no encontrado: {model_name}")
        config = dict(models[model_name])
        return config

    def get_default_model(self) -> str:
        models = self.data.get("llm_models", {})
        for name, cfg in models.items():
            if cfg.get("default"):
                return name
        return list(models.keys())[0] if models else None

    def list_models(self) -> list:
        models = self.data.get("llm_models", {})
        return [{"name": k, "description": v.get("description", "")} for k, v in models.items()]

    def get_mcp_servers(self) -> Dict[str, Any]:
        return self.data.get("mcp_servers", {})

    def get_enabled_tools(self) -> list:
        return self.data.get("tools_to_enable", [])

_config_instance = None

def get_config() -> Config:
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance
