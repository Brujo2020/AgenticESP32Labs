#!/usr/bin/env python3
"""
Gestor de MCP. Activa, desactiva y prueba servidores sin editar YAML a mano.

    python3 mcps_cli.py            menu interactivo
    python3 mcps_cli.py --listar   estado de todos
    python3 mcps_cli.py --probar   arranca cada activo y lista sus herramientas
"""
import asyncio, os, sys, shutil
from pathlib import Path

import yaml

AQUI = Path(__file__).parent
CATALOGO = AQUI / "mcp_catalogo.yaml"
CONFIG = AQUI / "config.yaml"

sys.path.insert(0, str(AQUI))
from nucleo.entorno import carga_env
carga_env()   # servidor/.env, si existe — asi env_pendiente() ve las claves de panel.py

V, R, A, Z, G, N = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[90m", "\033[0m"


def carga():
    cat = yaml.safe_load(CATALOGO.read_text()) or {}
    cfg = yaml.safe_load(CONFIG.read_text()) or {}
    return cat, cfg


def guarda(cfg):
    # Se reescribe solo lo necesario para no perder los comentarios del resto
    texto = CONFIG.read_text()
    nuevo = yaml.safe_dump({"mcp_servers": cfg["mcp_servers"],
                            "tools_to_enable": cfg["tools_to_enable"]},
                           allow_unicode=True, sort_keys=False)
    import re
    texto = re.sub(r'\nmcp_servers:.*?(?=\nwebsocket:)', "\n" + nuevo + "\n", texto, flags=re.S)
    CONFIG.write_text(texto)


def falta_binario(cmd):
    return shutil.which(cmd[0]) is None


def env_pendiente(entrada):
    faltan = []
    for _, v in (entrada.get("env") or {}).items():
        if isinstance(v, str) and v.startswith("${") and not os.getenv(v[2:-1]):
            faltan.append(v[2:-1])
    for parte in entrada.get("command", []):
        if isinstance(parte, str) and parte.startswith("${") and not os.getenv(parte[2:-1]):
            faltan.append(parte[2:-1])
    return faltan


def estado(nombre, entrada, activos):
    if nombre in activos:
        return f"{V}ACTIVO{N}"
    if falta_binario(entrada["command"]):
        return f"{R}falta {entrada['command'][0]}{N}"
    if env_pendiente(entrada):
        return f"{A}falta {', '.join(env_pendiente(entrada))}{N}"
    return f"{G}apagado{N}"


def listar():
    cat, cfg = carga()
    activos = cfg.get("tools_to_enable", []) or []
    print(f"\n{Z}=== MCP disponibles ==={N}\n")
    orden = sorted(cat.items(), key=lambda kv: (kv[1].get("categoria", ""), kv[0]))
    ultima = None
    for i, (nombre, e) in enumerate(orden, 1):
        cat_act = e.get("categoria", "otros")
        if cat_act != ultima:
            print(f"{Z}-- {cat_act.upper()} --{N}")
            ultima = cat_act
        print(f" {i:2}. {nombre:16} {estado(nombre, e, activos):28} {G}{e.get('descripcion','')}{N}")
        if e.get("requisitos") and nombre not in activos:
            print(f"      {G}requiere: {e['requisitos']}{N}")
    print()
    return orden, cfg, activos


def alterna(numero, orden, cfg):
    nombre, entrada = orden[numero - 1]
    cfg.setdefault("mcp_servers", {})
    cfg.setdefault("tools_to_enable", [])
    if nombre in cfg["tools_to_enable"]:
        cfg["tools_to_enable"].remove(nombre)
        print(f"{A}{nombre} desactivado{N}")
    else:
        srv = {"type": "subprocess",
               "command": [str(p).replace("~", os.path.expanduser("~"))
                           for p in entrada["command"]]}
        if entrada.get("env"):
            srv["env"] = entrada["env"]
        if entrada.get("env_dir"):
            srv.setdefault("env", {})["PYTHONPATH"] = entrada["env_dir"]
        cfg["mcp_servers"][nombre] = srv
        cfg["tools_to_enable"].append(nombre)
        print(f"{V}{nombre} activado{N}")
        if entrada.get("requisitos"):
            print(f"  {A}recuerda: {entrada['requisitos']}{N}")
        for v in env_pendiente(entrada):
            print(f"  {R}falta la variable {v}{N}")
    guarda(cfg)


async def probar():
    """Arranca cada MCP activo y lista sus herramientas. Es la prueba real."""
    sys.path.insert(0, str(AQUI))
    from nucleo.mcp_pool import MCPPool
    pool = MCPPool(str(CONFIG))
    await pool.connect_all()
    print(f"\n{Z}=== herramientas expuestas ==={N}")
    por_servidor = {}
    for nombre, h in pool.herramientas.items():
        por_servidor.setdefault(h["servidor"], []).append(nombre)
    for srv, hs in por_servidor.items():
        print(f"\n {V}{srv}{N}  ({len(hs)})")
        for x in hs:
            print(f"   - {x}")
    if not por_servidor:
        print(f" {R}ninguna. Revisa los avisos de arriba.{N}")
    await pool.cerrar()


def menu():
    while True:
        orden, cfg, _ = listar()
        print(f"{Z}numero{N} = activar/desactivar   {Z}p{N} = probar   {Z}q{N} = salir")
        op = input("> ").strip().lower()
        if op in ("q", "salir", ""):
            break
        if op == "p":
            asyncio.run(probar())
            input("\nintro para continuar...")
            continue
        if op.isdigit() and 1 <= int(op) <= len(orden):
            alterna(int(op), orden, cfg)
        else:
            print(f"{R}opcion no valida{N}")


if __name__ == "__main__":
    if "--listar" in sys.argv:
        listar()
    elif "--probar" in sys.argv:
        asyncio.run(probar())
    else:
        menu()
