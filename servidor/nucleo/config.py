import os
import yaml
from pathlib import Path
from typing import Any, Dict, Optional

class Config:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self.data = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config: {self.config_path}")
        with open(self.config_path, "r") as f:
            return yaml.safe_load(f)

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
