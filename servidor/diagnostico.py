#!/usr/bin/env python3
"""
Diagnostico de un vistazo: prueba cada capa por separado y dice cual falla.

    cd ~/AgenticESP32Labs/servidor && venv/bin/python3 diagnostico.py

Nace de una sesion entera perdida adivinando: el panel decia "no se pudo
hablar con el bridge" y eso podia ser el bridge caido, el ESP32 apagado, el
LLM sin cuota, el TTS sin voz instalada o el RSS con 403 -- todos daban el
mismo mensaje opaco. Aqui cada capa se prueba sola y se reporta aparte, sin
tocar nada ni depender de las demas.
"""
import asyncio
import json
import sys
from pathlib import Path

AQUI = Path(__file__).parent
sys.path.insert(0, str(AQUI))

from nucleo.entorno import carga_env  # noqa: E402

carga_env()

OK, MAL, AVISO = "\033[92m OK \033[0m", "\033[91mMAL \033[0m", "\033[93mAVISO\033[0m"


def linea(estado, titulo, detalle=""):
    print(f"[{estado}] {titulo}" + (f"\n         {detalle}" if detalle else ""))


async def prueba_rss():
    try:
        from noticias import titulares_detallado
        d = await titulares_detallado(3)
    except Exception as e:
        return linea(MAL, "Noticias (RSS)", f"excepcion: {e}")
    if d["titulares"]:
        linea(OK, f"Noticias (RSS) — {len(d['titulares'])} titulares",
              d["titulares"][0])
        if d["errores"]:
            linea(AVISO, "  algunas fuentes fallaron", " | ".join(d["errores"]))
    else:
        linea(MAL, "Noticias (RSS) — ninguna fuente respondio",
              " | ".join(d["errores"]) or "sin detalle")


async def prueba_llm():
    try:
        from proveedores import cadenas_desde_config
        import panel
        cadena = cadenas_desde_config(panel._config()).get("llm")
    except Exception as e:
        return linea(MAL, "LLM", f"no se pudo construir la cadena: {e}")
    if not cadena or not cadena.miembros:
        return linea(MAL, "LLM", "no hay ningun proveedor disponible")
    nombres = [p.nombre for p in cadena.miembros]
    try:
        msg = await asyncio.wait_for(cadena.chat_completo(
            [{"role": "user", "content": "Responde solo: listo"}], None), timeout=25)
        texto = (msg.get("content") or "").strip()
        linea(OK, f"LLM — cadena {nombres}", f"respondio: {texto[:60]!r}")
    except Exception as e:
        linea(MAL, f"LLM — cadena {nombres}", str(e)[:300])


def prueba_tts():
    try:
        from proveedores import cadenas_desde_config
        import panel
        cadena = cadenas_desde_config(panel._config()).get("tts")
    except Exception as e:
        return linea(MAL, "TTS", f"no se pudo construir la cadena: {e}")
    if not cadena or not cadena.miembros:
        return linea(MAL, "TTS", "no hay ningun proveedor disponible")
    nombres = [p.nombre for p in cadena.miembros]
    try:
        audio = cadena.sintetizar("Prueba de voz.", 24000)
        if audio:
            linea(OK, f"TTS — cadena {nombres}", f"{len(audio)} bytes de audio")
        else:
            linea(MAL, f"TTS — cadena {nombres}", "no devolvio audio")
    except Exception as e:
        linea(MAL, f"TTS — cadena {nombres}", str(e)[:300])


async def prueba_bridge():
    """Se conecta al bridge como cliente de control y pide 'estado'.

    Es exactamente lo que hace el panel, asi que si esto funciona y el panel
    no, el problema esta en el panel; si esto falla, esta en el bridge.
    """
    import websockets
    destino = "ws://127.0.0.1:8765"
    try:
        async with websockets.connect(destino, open_timeout=5) as ws:
            await ws.send(json.dumps({"t": "hola", "rol": "control"}))
            await ws.send(json.dumps({"t": "cmd", "rid": 1, "fn": "estado", "args": {}}))
            async for msg in ws:
                if isinstance(msg, bytes):
                    continue
                d = json.loads(msg)
                if d.get("t") == "res":
                    v = d.get("v")
                    linea(OK, "Bridge de voz (8765) — responde")
                    if isinstance(v, dict) and v.get("conectado") is False:
                        linea(AVISO, "ESP32", f"no conectado: {v.get('motivo', '')}")
                    elif isinstance(v, dict):
                        linea(OK, "ESP32 — conectado", json.dumps(v, ensure_ascii=False)[:160])
                    else:
                        linea(AVISO, "ESP32", str(v)[:160])
                    return
            linea(MAL, "Bridge de voz (8765)", "conecto pero no respondio 'res'")
    except Exception as e:
        linea(MAL, f"Bridge de voz ({destino})", f"{type(e).__name__}: {e}")


async def principal():
    print("\n=== Diagnostico del asistente ESP32 ===\n")
    await prueba_rss()
    await prueba_llm()
    prueba_tts()
    await prueba_bridge()
    print("\nLeyenda: MAL = esa capa es la que hay que arreglar.")
    print("El panel solo puede hablar/mostrar si 'Bridge' y 'ESP32' estan OK.\n")


if __name__ == "__main__":
    asyncio.run(principal())
