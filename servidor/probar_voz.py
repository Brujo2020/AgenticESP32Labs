#!/usr/bin/env python3
"""
Prueba la voz sin necesidad del ESP32.

Responde a la pregunta "¿por que no suena?" sin tener que deducirla del log:
dice que proveedor de TTS gana, por que los demas quedaron descartados, y
guarda un WAV que puedes escuchar.

    python3 probar_voz.py                     frase de ejemplo
    python3 probar_voz.py "lo que quieras"    tu propio texto
    python3 probar_voz.py --stt grabacion.wav prueba tambien el reconocimiento
"""
import sys
import wave
from pathlib import Path

AQUI = Path(__file__).parent
sys.path.insert(0, str(AQUI))

from nucleo.entorno import carga_env  # noqa: E402
carga_env()

import yaml  # noqa: E402
from proveedores import cadenas_desde_config  # noqa: E402

V, R, A, Z, G, N = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[90m", "\033[0m"
SAMPLE_RATE = 24000


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    texto = args[0] if args else (
        "Hola Mario. Si oyes esto con acento espanol, el text to speech "
        "esta funcionando como debe.")

    cfg = yaml.safe_load((AQUI / "config.yaml").read_text())
    cadenas = cadenas_desde_config(cfg)
    cadena = cadenas.get("tts")

    print(f"\n{Z}=== Proveedores de voz ==={N}")
    if not cadena or not cadena.miembros:
        print(f"{R}No hay ningun TTS configurado en config.yaml.{N}")
        return 1

    for p in cadena.miembros:
        try:
            listo = p.disponible()
        except Exception as e:
            listo, motivo = False, str(e)
        else:
            motivo = ""
        marca = f"{V}LISTO{N}" if listo else f"{R}descartado{N}"
        extra = f"  {G}{motivo}{N}" if motivo else ""
        print(f"  {p.nombre:14} {marca}{extra}")

    activo = cadena.activo
    if not activo:
        print(f"\n{R}Ninguno disponible: por eso no suena nada.{N}")
        print(f"{A}Lo mas probable: falta instalar Piper.{N}")
        print(f"  ./venv/bin/pip install piper-tts")
        print(f"  y descargar la voz es_ES a ~/piper-voces/")
        return 1

    print(f"\n{Z}Se usara:{N} {V}{activo.nombre}{N}")
    if "english" in str(getattr(activo, "model", "")).lower():
        print(f"{A}AVISO: ese modelo es de INGLES. Leera el espanol con "
              f"fonetica inglesa y sonara mal.{N}")

    print(f"\n{Z}Sintetizando...{N} {G}{texto[:70]}{N}")
    try:
        pcm = cadena.sintetizar(texto, SAMPLE_RATE)
    except Exception as e:
        print(f"{R}Fallo la sintesis: {e}{N}")
        return 1

    if not pcm:
        print(f"{R}Devolvio audio vacio.{N}")
        return 1

    salida = AQUI / "prueba_voz.wav"
    with wave.open(str(salida), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)

    seg = len(pcm) / 2 / SAMPLE_RATE
    print(f"\n{V}OK{N}  {len(pcm)} bytes, {seg:.1f} segundos")
    print(f"    guardado en {salida}")
    print(f"\n{Z}Para escucharlo:{N}")
    print(f"  en el servidor:  aplay {salida.name}   {G}(o: sudo apt install alsa-utils){N}")
    print(f"  en tu Mac:       scp ubuntu@IP:{salida} . && afplay prueba_voz.wav")
    return 0


if __name__ == "__main__":
    sys.exit(main())
