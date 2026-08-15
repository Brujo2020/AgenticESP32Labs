#!/usr/bin/env bash
# ============================================================
#  probar_v010.sh — flashea el firmware de v0.1.0, el que SI se
#  valido en placa, para separar "mis cambios" de "la placa".
#
#  No toca tu copia de trabajo: usa un worktree aparte en /tmp.
#  develop se queda intacto y no pierdes nada.
#
#  Resultado del experimento:
#    enciende  -> algo de lo posterior a v0.1.0 rompio el firmware
#    negra     -> no es el firmware; es hardware o el flujo de flasheo
# ============================================================
set -e
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WT="/tmp/hud-v010"

. "$PROJ/entorno.sh"

echo
echo "== Preparando copia limpia de v0.1.0 en $WT =="
git -C "$PROJ" worktree remove --force "$WT" 2>/dev/null || true
rm -rf "$WT"
git -C "$PROJ" worktree add --detach "$WT" v0.1.0
cd "$WT"

# En v0.1.0 el WiFi todavia estaba en el codigo, asi que no hace falta
# ninguna configuracion: compila y arranca tal cual.
echo
echo "== Compilando v0.1.0 =="
idf.py build > "$WT/build.log" 2>&1 || { echo "FALLO:"; tail -25 "$WT/build.log"; exit 1; }
echo "   build OK"

echo
echo "== Conecta la placa con BOOT pulsado, luego suelta BOOT =="
PORT=""
for i in $(seq 1 60); do
    PORT=$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)
    [ -n "$PORT" ] && break
    sleep 1
done
[ -z "$PORT" ] && { echo "No aparecio ningun puerto."; exit 1; }
echo "   Puerto: $PORT"

echo
echo "== Flasheando =="
idf.py -p "$PORT" flash > "$WT/flash.log" 2>&1 || { echo "FALLO:"; tail -15 "$WT/flash.log"; exit 1; }
echo "   flash OK"

echo
echo "== Reiniciando en modo aplicacion =="
esptool.py -p "$PORT" --after hard_reset chip_id > /dev/null 2>&1 || true

cat <<'FIN'

------------------------------------------------------------
  MIRA LA PANTALLA AHORA. No abras el monitor: al abrirlo,
  macOS afirma DTR y en este chip eso tira GPIO0 a masa,
  devolviendo la placa a modo descarga.

  ENCIENDE  -> el hardware esta bien y algo posterior a v0.1.0
               rompio el firmware. Se bisecta commit a commit.

  NEGRA     -> no es el firmware: v0.1.0 es la version que tu
               mismo validaste en placa. Toca mirar hardware o
               el flujo de flasheo.
------------------------------------------------------------

Para volver a tu rama de trabajo:
    git worktree remove --force /tmp/hud-v010
FIN
