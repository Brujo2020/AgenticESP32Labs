#!/usr/bin/env bash
# ============================================================
#  entorno.sh — carga el entorno de ESP-IDF en la shell actual
#
#  Uso:   source ./entorno.sh      (o:  . ./entorno.sh)
#  Luego: idf.py monitor / idf.py build / lo que sea
#
#  Existe porque `idf.py` no esta en el PATH hasta que se ejecuta
#  export.sh, y aqui ese paso tiene una trampa: el python3 del sistema
#  es 3.14, pero el entorno instalado de ESP-IDF es py3.11. export.sh
#  deduce el nombre del venv del primer python3 que encuentra, asi que
#  busca "idf6.0_py3.14_env", no lo halla y aborta. Por eso hay que
#  anteponer el bin del venv correcto ANTES de llamarlo.
#
#  run.sh y monitor.sh usan este mismo fichero: la logica vive en un
#  sitio, no duplicada en cada script.
# ============================================================

export IDF_PATH="${IDF_PATH:-$HOME/esp/esp-idf}"
export IDF_TOOLS_PATH="${IDF_TOOLS_PATH:-$HOME/.espressif}"

_ENV_DIR=$(ls -d "$IDF_TOOLS_PATH"/python_env/idf*_env 2>/dev/null | head -1)
if [ -z "$_ENV_DIR" ]; then
    echo "No hay entorno python de ESP-IDF en $IDF_TOOLS_PATH/python_env"
    echo "Ejecuta:  \$IDF_PATH/install.sh esp32s3"
    return 1 2>/dev/null || exit 1
fi

export PATH="$_ENV_DIR/bin:$PATH"
export IDF_PYTHON_ENV_PATH="$_ENV_DIR"

if [ -f "$IDF_PATH/version.txt" ]; then
    export ESP_IDF_VERSION=$(cut -d- -f1 "$IDF_PATH/version.txt" | sed 's/^v//' | cut -d. -f1,2)
else
    export ESP_IDF_VERSION="6.0"
fi

# export.sh es ruidoso; su salida solo interesa si algo falla
_SALIDA=$(. "$IDF_PATH/export.sh" 2>&1)

# export.sh puede pisar estas variables o dejarlas vacias
[ -z "$IDF_PYTHON_ENV_PATH" ] && export IDF_PYTHON_ENV_PATH="$_ENV_DIR"
[ -z "$ESP_IDF_VERSION" ] && export ESP_IDF_VERSION="6.0"

if ! command -v idf.py >/dev/null 2>&1; then
    echo "FALLO al cargar el entorno de ESP-IDF:"
    echo "$_SALIDA" | tail -12
    return 1 2>/dev/null || exit 1
fi

echo "ESP-IDF listo · venv $(basename "$_ENV_DIR") · $(command -v idf.py)"
unset _ENV_DIR _SALIDA
