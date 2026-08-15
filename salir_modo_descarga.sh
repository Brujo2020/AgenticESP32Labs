#!/usr/bin/env bash
# ============================================================
#  salir_modo_descarga.sh — fuerza un reinicio en modo aplicacion
#
#  Cuando la placa se queda en boot:0x23 (DOWNLOAD) no ejecuta el
#  firmware y la pantalla queda negra. esptool termina con un
#  hard_reset que deja GPIO0 en alto, asi que arranca la app.
# ============================================================
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$PROJ/entorno.sh" || exit 1

PORT="${1:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)}"
[ -z "$PORT" ] && { echo "No hay ningun /dev/cu.usbmodem*"; exit 1; }

echo "Reiniciando $PORT en modo aplicacion..."
esptool.py -p "$PORT" --after hard_reset chip_id
echo
echo "Mira la pantalla de la placa AHORA, sin abrir el monitor:"
echo "abrirlo volveria a afirmar DTR y la devolveria a modo descarga."
