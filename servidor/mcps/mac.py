#!/usr/bin/env python3
"""
MCP: telemetria del Mac. Lo que esta pasando en la maquina ahora mismo.
Sin dependencias externas: todo sale de herramientas del sistema.
"""
import re, shutil, subprocess
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mac")


def _sh(cmd: str, timeout: int = 6) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True,
                              text=True, timeout=timeout).stdout
    except Exception:
        return ""


@mcp.tool()
def memoria() -> dict:
    """Memoria RAM: total, usada y presion de memoria."""
    total = int(_sh("sysctl -n hw.memsize") or 0)
    vm = _sh("vm_stat")
    pag = 16384 if "16384" in _sh("sysctl -n vm.pagesize") else 4096
    def campo(n):
        m = re.search(rf"{n}:\s+(\d+)", vm)
        return int(m.group(1)) * pag if m else 0
    libre = campo("Pages free") + campo("Pages inactive")
    usada = total - libre
    return {
        "total_gb": round(total / 1e9, 1),
        "usada_gb": round(usada / 1e9, 1),
        "libre_gb": round(libre / 1e9, 1),
        "uso_pct": round(usada / total * 100) if total else 0,
    }


@mcp.tool()
def cpu() -> dict:
    """Carga de CPU y numero de nucleos."""
    top = _sh("top -l 1 -n 0")
    m = re.search(r"CPU usage:\s+([\d.]+)% user,\s+([\d.]+)% sys,\s+([\d.]+)% idle", top)
    return {
        "nucleos": int(_sh("sysctl -n hw.ncpu") or 0),
        "usuario_pct": float(m.group(1)) if m else 0.0,
        "sistema_pct": float(m.group(2)) if m else 0.0,
        "ocupado_pct": round(100 - float(m.group(3)), 1) if m else 0.0,
        "modelo": _sh("sysctl -n machdep.cpu.brand_string").strip(),
    }


@mcp.tool()
def gpu() -> dict:
    """GPU: modelo y nucleos. El porcentaje de uso exige permisos de root."""
    info = _sh("system_profiler SPDisplaysDataType", timeout=15)
    modelo = re.search(r"Chipset Model:\s*(.+)", info)
    nucleos = re.search(r"Total Number of Cores:\s*(\d+)", info)
    return {
        "modelo": modelo.group(1).strip() if modelo else "N D",
        "nucleos": int(nucleos.group(1)) if nucleos else 0,
        "uso_pct": None,
        "nota": "el uso de GPU en Apple Silicon requiere powermetrics con sudo",
    }


@mcp.tool()
def procesos(cuantos: int = 5) -> list:
    """Los procesos que mas CPU consumen y para que sirve cada uno."""
    QUE_HACE = {
        "WindowServer": "compositor grafico de macOS",
        "kernel_task": "nucleo del sistema y control termico",
        "Blender": "modelado y render 3D",
        "Unity": "motor de videojuegos",
        "Code": "editor VSCode",
        "python3": "script de Python",
        "node": "proceso Node.js",
        "Google Chrome": "navegador",
        "mlx": "inferencia local de modelos",
        "Xcode": "entorno de desarrollo de Apple",
        "Docker": "contenedores",
    }
    salida = []
    for linea in _sh(f"ps -Aceo pcpu,pmem,comm -r | head -n {cuantos + 1}").splitlines()[1:]:
        partes = linea.split(None, 2)
        if len(partes) < 3:
            continue
        cpu_pct, mem_pct, nombre = partes
        salida.append({
            "proceso": nombre.strip(),
            "cpu_pct": float(cpu_pct),
            "mem_pct": float(mem_pct),
            "hace": next((v for k, v in QUE_HACE.items()
                          if k.lower() in nombre.lower()), "aplicacion de usuario"),
        })
    return salida


@mcp.tool()
def disco() -> dict:
    """Espacio libre en el disco principal."""
    u = shutil.disk_usage("/")
    return {"total_gb": round(u.total / 1e9), "libre_gb": round(u.free / 1e9),
            "uso_pct": round(u.used / u.total * 100)}


@mcp.tool()
def bateria() -> dict:
    """Carga de la bateria y si esta enchufado."""
    s = _sh("pmset -g batt")
    pct = re.search(r"(\d+)%", s)
    return {"porcentaje": int(pct.group(1)) if pct else None,
            "enchufado": "AC Power" in s}


if __name__ == "__main__":
    mcp.run()
