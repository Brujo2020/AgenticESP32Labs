#!/usr/bin/env python3
"""
Comprueba que el simulador y el firmware comparten la misma geometria.

El simulador vale exactamente lo que valga su fidelidad. Si hud.c y
simulador.html divergen, deja de ser una herramienta y pasa a ser un dibujo
bonito que engana: se toman decisiones de interfaz sobre algo que no es lo
que la placa va a mostrar.

Esta comprobacion es barata y detecta la divergencia en el momento, no tres
sesiones despues cuando ya nadie recuerda cual de los dos era el bueno.

    python3 tools/verifica_sincronia.py
"""
import os, re, sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUD = os.path.join(RAIZ, "components/hud/hud.c")
SIM = os.path.join(RAIZ, "tools/simulador.html")


def busca(txt, patron, nombre):
    m = re.search(patron, txt)
    if not m:
        print(f"  no se encontro '{nombre}' con el patron {patron!r}")
        return None
    return m.group(1)


def main():
    c = open(HUD, encoding="utf-8").read()
    j = open(SIM, encoding="utf-8").read()

    pares = [
        ("MARGEN_IZQ",  r"#define MARGEN_IZQ\s+(\d+)",              r"MARGEN_IZQ=(\d+)"),
        ("MARGEN_DER",  r"#define MARGEN_DER\s+(\d+)",              r"MARGEN_DER=(\d+)"),
        ("B_PREV.x",    r"B_PREV = \{ \.x = (\d+)",                 r"B_PREV=\{x:(\d+)"),
        ("B_PREV.w",    r"B_PREV[\s\S]{0,80}?\.w = (\d+)",          r"B_PREV=\{x:\d+,y:\d+,w:(\d+)"),
        ("B_NEXT.x",    r"B_NEXT = \{ \.x = (\d+)",                 r"B_NEXT=\{x:(\d+)"),
        ("B_NEXT.w",    r"B_NEXT[\s\S]{0,80}?\.w = (\d+)",          r"B_NEXT=\{x:\d+,y:\d+,w:(\d+)"),
        ("boton VOZ y", r"\.x = 100, \.y = (\d+), \.w = 40",        r"const y=FIX\?182:(\d+)"),
        ("radio util",  r"semi2 = (\d+) \* \d+ - dy",               r"118\*(118)-dy\*dy"),
    ]

    fallos = 0
    print(f"{'constante':<14} {'hud.c':>8} {'simulador':>10}")
    print("-" * 38)
    for nombre, pc, pj in pares:
        a, b = busca(c, pc, nombre), busca(j, pj, nombre)
        igual = a is not None and a == b
        if not igual:
            fallos += 1
        print(f"{nombre:<14} {str(a):>8} {str(b):>10}   {'ok' if igual else 'DIFIERE'}")

    # Bandas tactiles de AJUSTES: cuatro umbrales, en el mismo orden
    bc = re.findall(r"y < (\d+)\)\s+s_sel", c)
    bj = busca(j, r"BANDAS_FIX=\[([\d,]+)\]", "BANDAS_FIX")
    bj = bj.split(",") if bj else []
    igual = bc == bj and len(bc) == 4
    if not igual:
        fallos += 1
    print(f"\n{'bandas AJUSTES':<14} {','.join(bc):>8} {','.join(bj):>10}   "
          f"{'ok' if igual else 'DIFIERE'}")

    # La paleta tiene que ser la misma o los colores del simulador mienten
    pal = os.path.join(RAIZ, "components/display/include/display.h")
    d = open(pal, encoding="utf-8").read()
    print()
    for color in ("C_CYAN", "C_MAGENTA", "C_LIME", "C_AMBER", "C_ICE",
                  "C_BLOOD", "C_GREY", "C_WHITE", "C_VOID"):
        a = busca(d, rf"#define {color}\s+(0x[0-9A-Fa-f]+)", color)
        b = busca(j, rf"{color}=(0x[0-9A-Fa-f]+)", color)
        igual = a is not None and b is not None and int(a, 16) == int(b, 16)
        if not igual:
            fallos += 1
            print(f"  {color:<10} {a} vs {b}   DIFIERE")
    print("  paleta: todos los colores coinciden" if fallos == 0 else "")

    print()
    if fallos:
        print(f"{fallos} divergencia(s). El simulador ya no representa al firmware.")
        return 1
    print("*** SIMULADOR Y FIRMWARE SINCRONIZADOS ***")
    return 0


if __name__ == "__main__":
    sys.exit(main())
