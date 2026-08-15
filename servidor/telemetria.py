"""
Lineas cortas de telemetria para la pantalla circular.
Reutiliza las mismas funciones que expone el MCP 'mac'.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "mcps"))

ANCHO = 33


def _mod():
    import importlib.util
    ruta = os.path.join(os.path.dirname(__file__), "mcps", "mac.py")
    spec = importlib.util.spec_from_file_location("mac_tools", ruta)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def lineas_mac() -> list[str]:
    try:
        m = _mod()
        # Las funciones van envueltas por @mcp.tool(): se llama a la original
        mem = m.memoria.fn() if hasattr(m.memoria, "fn") else m.memoria()
        cpu = m.cpu.fn() if hasattr(m.cpu, "fn") else m.cpu()
        gpu = m.gpu.fn() if hasattr(m.gpu, "fn") else m.gpu()
        proc = m.procesos.fn(3) if hasattr(m.procesos, "fn") else m.procesos(3)
        bat = m.bateria.fn() if hasattr(m.bateria, "fn") else m.bateria()

        out = [
            f"RAM {mem['usada_gb']}/{mem['total_gb']}GB {mem['uso_pct']}%",
            f"CPU {cpu['ocupado_pct']}% {cpu['nucleos']} NUCLEOS",
            f"GPU {gpu['modelo'][:20]}",
            f"BAT {bat['porcentaje']}%" + (" ENCHUFADO" if bat["enchufado"] else ""),
        ]
        for p in proc[:3]:
            out.append(f"{p['proceso'][:14]} {p['cpu_pct']:.0f}%")
        return [l.upper()[:ANCHO] for l in out]
    except Exception as e:
        return [f"ERROR TELEMETRIA"[:ANCHO], str(e).upper()[:ANCHO]]


def lineas_creativo() -> list[str]:
    """Estado de Unity y Blender: si estan abiertos y en que proyecto."""
    import subprocess, re
    out = []
    try:
        ps = subprocess.run("ps -Aceo pcpu,comm", shell=True, capture_output=True,
                            text=True, timeout=5).stdout

        for app, etiqueta in (("Blender", "BLENDER"), ("Unity", "UNITY")):
            filas = [l for l in ps.splitlines() if app.lower() in l.lower()]
            if filas:
                cpu = sum(float(l.split()[0]) for l in filas)
                out.append(f"{etiqueta} ACTIVO {cpu:.0f}% CPU")
            else:
                out.append(f"{etiqueta} CERRADO")

        # Documento abierto, si el MCP correspondiente lo expone
        docs = subprocess.run(
            "lsof -c Blender -c Unity 2>/dev/null | grep -oE '[^ ]+\\.(blend|unity)$' | head -2",
            shell=True, capture_output=True, text=True, timeout=6).stdout
        for d in docs.split():
            out.append(os.path.basename(d)[:ANCHO])
    except Exception as e:
        out.append("ERROR")
        out.append(str(e)[:ANCHO])
    return [l.upper()[:ANCHO] for l in out][:6]


if __name__ == "__main__":
    print("--- MAC ---");      [print(l) for l in lineas_mac()]
    print("--- CREATIVO ---"); [print(l) for l in lineas_creativo()]
