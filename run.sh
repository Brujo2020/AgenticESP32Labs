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
export IDF_PATH="$HOME/esp/esp-idf"
export IDF_TOOLS_PATH="$HOME/.espressif"

# El python3 del sistema es 3.14, pero el entorno instalado de ESP-IDF es py3.11.
# export.sh deduce el nombre del venv a partir del python3 que encuentre primero,
# asi que hay que anteponer el del venv o busca "idf6.0_py3.14_env" y aborta.
ENV_DIR=$(ls -d "$IDF_TOOLS_PATH"/python_env/idf*_env 2>/dev/null | head -1)
if [ -z "$ENV_DIR" ]; then
    echo "   No hay entorno python de ESP-IDF en $IDF_TOOLS_PATH/python_env"
    echo "   Ejecuta:  \$IDF_PATH/install.sh esp32s3"
    exit 1
fi
export PATH="$ENV_DIR/bin:$PATH"
echo "   venv: $(basename "$ENV_DIR")  ($(python3 --version 2>&1))"

export IDF_PYTHON_ENV_PATH="$ENV_DIR"
if [ -f "$IDF_PATH/version.txt" ]; then
    export ESP_IDF_VERSION=$(cut -d- -f1 "$IDF_PATH/version.txt" | sed 's/^v//' | cut -d. -f1,2)
else
    export ESP_IDF_VERSION="6.0"
fi
echo "   IDF_PYTHON_ENV_PATH=$IDF_PYTHON_ENV_PATH"
echo "   ESP_IDF_VERSION=$ESP_IDF_VERSION"

. "$IDF_PATH/export.sh" > "$PROJ/env_out.log" 2>&1
# export.sh puede pisarlas o dejarlas vacias: se reponen
[ -z "$IDF_PYTHON_ENV_PATH" ] && export IDF_PYTHON_ENV_PATH="$ENV_DIR"
[ -z "$ESP_IDF_VERSION" ] && export ESP_IDF_VERSION="6.0"

if ! command -v idf.py >/dev/null 2>&1; then
    echo "   FALLO el entorno. Ultimas lineas de env_out.log:"
    tail -12 "$PROJ/env_out.log"
    exit 1
fi
echo "   entorno OK ($(command -v idf.py))"

echo "== 2/5  Compilando =="
# Credenciales WiFi de siembra, opcionales:
#
#     WIFI_SSID="MiRed" WIFI_PASS="miclave" ./run.sh
#
# Solo hacen falta la PRIMERA vez que se flashea una placa, o si se le borro
# la memoria: despues las redes viven en NVS (ver wifi_redes.h) y se anaden
# desde el panel. Se pasan por linea de comandos y no en un fichero para que
# la contrasena del WiFi no acabe versionada, que es donde estuvo hasta ahora.
#
# Se usa un array y NO eval: con eval, una clave con comillas o $ rompia el
# comando o se expandia sola. Un array pasa cada argumento tal cual.
EXTRA=()
if [ -n "$WIFI_SSID" ]; then
    # Las comillas y barras del SSID o la clave se escapan: el valor acaba
    # dentro de un literal de C, y una comilla suelta no compilaria.
    _ss=${WIFI_SSID//\\/\\\\}; _ss=${_ss//\"/\\\"}
    _pw=${WIFI_PASS//\\/\\\\}; _pw=${_pw//\"/\\\"}
    EXTRA+=("-DWIFI_SSID_DEFECTO=\"$_ss\"")
    EXTRA+=("-DWIFI_PASS_DEFECTO=\"$_pw\"")
    echo "   sembrando la red '$WIFI_SSID' (solo si la placa no tiene ninguna)"
fi
idf.py build "${EXTRA[@]}" > "$PROJ/build_out.log" 2>&1
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
echo "   Desconecta y reconecta el cable SIN tocar botones."
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
