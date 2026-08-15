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
echo "Si sale  boot:0x23 (DOWNLOAD(USB/UART0))  y 'waiting for download',"
echo "la placa NO esta ejecutando el firmware: se quedo en modo descarga."
echo "No es un fallo de codigo. Desconecta el cable del todo, espera 3s y"
echo "vuelve a conectarlo SIN tocar BOOT. El arranque normal es  boot:0x2b."
echo "------------------------------------------------------------"
idf.py -p "$PORT" monitor
