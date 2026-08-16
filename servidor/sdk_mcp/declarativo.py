"""
MCP declarativos: envuelven una API REST u OpenAI-compatible sin escribir
una clase Python, con una entrada en mcp_catalogo.yaml.

    mi-api:
      categoria: local
      descripcion: "..."
      declarativo:
        base_url: "https://api.ejemplo.com"
        headers: { Authorization: "Bearer ${MI_TOKEN}" }
        tools:
          - nombre: buscar
            descripcion: "Busca algo en la API"
            metodo: GET
            ruta: "/buscar"
            parametros:
              q: {tipo: string, requerido: true, en: query}

No todos los MCP encajan aqui (los que necesitan estado, streams o logica
condicional siguen siendo un mcps/*.py sobre MCPBase) pero cubre el caso mas
comun: "pega esta API a una tool".
"""
import os
from typing import Any

import httpx

from .base import MCPBase


def _expande(v):
    if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
        return os.getenv(v[2:-1], "")
    return v


def construir(nombre: str, spec: dict) -> MCPBase:
    m = MCPBase(nombre)
    base_url = spec["base_url"].rstrip("/")
    headers = {k: _expande(v) for k, v in (spec.get("headers") or {}).items()}

    for t in spec.get("tools", []):
        _registra_tool(m, base_url, headers, t)

    return m


def _registra_tool(m: MCPBase, base_url: str, headers: dict, t: dict):
    ruta = t["ruta"]
    metodo = t.get("metodo", "GET").upper()
    params_spec = t.get("parametros", {})

    async def tool_generica(**kwargs) -> Any:
        query, cuerpo, ruta_final = {}, {}, ruta
        for nombre_p, cfg_p in params_spec.items():
            if nombre_p not in kwargs:
                continue
            valor = kwargs[nombre_p]
            en = cfg_p.get("en", "query")
            if en == "query":
                query[nombre_p] = valor
            elif en == "body":
                cuerpo[nombre_p] = valor
            elif en == "path":
                ruta_final = ruta_final.replace(f"{{{nombre_p}}}", str(valor))

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.request(
                metodo, base_url + ruta_final, headers=headers,
                params=query or None, json=cuerpo or None,
            )
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError:
                return {"texto": resp.text}

    tool_generica.__name__ = t["nombre"]
    tool_generica.__doc__ = t.get("descripcion", "")
    m.tool()(tool_generica)


# LIMITACION CONOCIDA (queda en specs/002-.../tasks.md como pendiente):
# tool_generica usa **kwargs sin anotaciones de tipo, asi que FastMCP no
# puede generar un JSON Schema preciso de los parametros a partir de la
# firma. Para el MVP el esquema que ve el modelo describe los parametros
# como "object" generico (funciona, pero sin validacion de tipos por
# parametro). Arreglo real: construir la firma dinamicamente con
# `inspect.Signature` a partir de `params_spec` antes de registrar la tool.
