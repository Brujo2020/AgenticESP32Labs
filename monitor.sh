#!/usr/bin/env bash
# ============================================================
#  monitor.sh — abre el monitor serie sin flashear nada
#  Uso:  ./monitor.sh  [puerto]
#
#  Para diagnosticar arranques: muestra panics, boot loops y si el
#  firmware llega siquiera a app_main. Salir con Ctrl+].
# ============================================================
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJ" || exit 1

. "$PROJ/entorno.sh" || exit 1

# --sin-reset: no toca DTR/RTS al abrir el puerto.
# En el S3 con USB nativo el chip interpreta esas lineas: RTS controla EN
# (reset) y DTR controla GPIO0 (boot). macOS afirma DTR al abrir el puerto,
# asi que abrir el monitor de la forma normal tira GPIO0 a masa y arranca en
# modo descarga. Con esta opcion se conecta sin tocarlas y se ve lo que la
# placa ya estaba haciendo.
SIN_RESET=""
if [ "$1" = "--sin-reset" ] || [ "$1" = "-s" ]; then
    SIN_RESET="--no-reset"
    shift
fi

PORT="$1"
if [ -z "$PORT" ]; then
    echo "Buscando puerto (30s)..."
    for i in $(seq 1 30); do
        PORT=$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)
        [ -n "$PORT" ] && break
        sleep 1
    done
fi

if [ -z "$PORT" ]; then
    echo
    echo "No aparece ningun /dev/cu.usbmodem*."
    echo "Eso significa que la placa no esta enumerando por USB. Antes de"
    echo "sospechar del firmware, descartar lo fisico:"
    echo "  - cable de DATOS, no de solo carga (es la causa mas comun)"
    echo "  - probar otro puerto USB, sin hub"
    echo "  - mantener BOOT pulsado al conectar: fuerza el modo descarga y"
    echo "    enumera aunque el firmware este en boot loop"
    echo
    echo "Puertos serie visibles ahora mismo:"
    ls -1 /dev/cu.* 2>/dev/null | sed 's/^/  /' || echo "  ninguno"
    exit 1
fi

echo "Puerto: $PORT"
echo "Pulsa el boton de RESET de la placa para ver el arranque."
echo "Salir: Ctrl+]"
echo
echo "boot:0x2b = arranque normal.  boot:0x23 = modo descarga (no ejecuta nada)."
echo
echo "Si sale 0x23 una y otra vez aunque hagas ciclos de alimentacion, es el"
echo "propio monitor: al abrir el puerto macOS afirma DTR, y en el S3 con USB"
echo "nativo DTR esta cableado a GPIO0. Prueba entonces:"
echo "    ./monitor.sh --sin-reset"
echo "y para sacarla del modo descarga:"
echo "    esptool.py -p $PORT --after hard_reset chip_id"
echo "------------------------------------------------------------"
idf.py -p "$PORT" monitor $SIN_RESET
