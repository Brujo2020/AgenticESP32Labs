#!/usr/bin/env bash
# ============================================================
#  run.sh — compila, flashea y captura el arranque en boot.log
#  Uso:  ./run.sh
# ============================================================
PROJ="$HOME/Proyectos/Development/esp32-hud-idf"
LOG="$PROJ/boot.log"
cd "$PROJ" || exit 1

echo "===== run.sh v3 ====="
echo "== 1/5  Cargando entorno ESP-IDF =="
# La logica vive en entorno.sh para que monitor.sh la reutilice
. "$PROJ/entorno.sh" || exit 1

# --- sdkconfig obsoleto ---
# Trampa clasica de ESP-IDF: sdkconfig.defaults SOLO se aplica cuando
# sdkconfig no existe. Editar defaults con un sdkconfig ya generado no
# cambia nada, y `idf.py fullclean` tampoco lo borra. Sintoma tipico:
# se revierte una opcion que rompia el arranque, se recompila, y la
# placa sigue rota porque el build usa el sdkconfig viejo.
if [ -f "$PROJ/sdkconfig" ] && [ "$PROJ/sdkconfig.defaults" -nt "$PROJ/sdkconfig" ]; then
    echo
    echo "   AVISO: sdkconfig.defaults es mas reciente que sdkconfig."
    echo "   El build va a IGNORAR los cambios de defaults."
    echo "   Para aplicarlos:  rm sdkconfig && ./run.sh"
    echo
    printf "   Borrar sdkconfig y regenerarlo ahora? [s/N] "
    read -r RESP
    case "$RESP" in
        [sSyY]) rm -f "$PROJ/sdkconfig"; echo "   sdkconfig borrado, se regenera desde defaults";;
        *) echo "   Se mantiene el sdkconfig actual";;
    esac
fi

echo "== 2/5  Compilando =="
idf.py build > "$PROJ/build_out.log" 2>&1
if [ $? -ne 0 ]; then
    echo "FALLO EL BUILD. Ultimas lineas:"
    tail -25 "$PROJ/build_out.log"
    exit 1
fi
echo "   build OK"

wait_port() {
    for i in $(seq 1 "$2"); do
        P=$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)
        [ -n "$P" ] && { echo "$P"; return 0; }
        sleep 1
    done
    return 1
}

echo
echo "== 3/5  MODO DESCARGA =="
echo "   Manten BOOT presionado, conecta el cable, suelta BOOT."
echo "   (esperando el puerto, 60s)"
PORT=$(wait_port x 60)
if [ -z "$PORT" ]; then echo "   No aparecio ningun puerto. Abortando."; exit 1; fi
echo "   Puerto: $PORT"

echo
echo "== 4/5  Flasheando =="
idf.py -p "$PORT" flash > "$PROJ/flash_out.log" 2>&1
if [ $? -ne 0 ]; then
    echo "   FALLO EL FLASH:"; tail -15 "$PROJ/flash_out.log"; exit 1
fi
echo "   flash OK"

echo
echo "== 5/5  ARRANQUE LIMPIO =="
echo "   Desconecta el cable DEL TODO, espera 3s y reconecta SIN tocar BOOT."
echo "   Este paso no es opcional: si el chip no ve un ciclo de alimentacion"
echo "   limpio se queda en modo descarga (boot:0x23) y no ejecuta nada,"
echo "   que desde fuera se ve como una pantalla negra."
sleep 3
PORT=$(wait_port x 60)
if [ -z "$PORT" ]; then
    echo "   El puerto NO reaparecio tras el reset."      | tee "$LOG"
    echo "   => la placa no enumera con el firmware nuevo" | tee -a "$LOG"
    exit 1
fi
echo "   Puerto: $PORT — capturando 25 segundos..."

python - "$PORT" "$LOG" <<'PY'
import sys, time, serial
port, logpath = sys.argv[1], sys.argv[2]
try:
    s = serial.Serial(port, 115200, timeout=1)
except Exception as e:
    open(logpath, "w").write("No se pudo abrir el puerto: %s\n" % e)
    sys.exit(0)
end = time.time() + 25
buf = b""
while time.time() < end:
    try:
        buf += s.read(4096)
    except Exception as e:
        buf += b"\n[lectura interrumpida: %s]\n" % str(e).encode()
        break
open(logpath, "wb").write(buf)
print("   capturados %d bytes" % len(buf))
PY

echo
echo "LISTO. Log guardado en: $LOG"
