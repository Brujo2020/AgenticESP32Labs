#!/usr/bin/env bash
# ============================================================
#  activar.sh — entorno virtual del servidor, aislado de todo lo demas
#
#  Por que existe: sin esto, `python3` resuelve al primero que encuentre en
#  el PATH — en este Mac, el de PlatformIO (~/.platformio/penv), que trae su
#  propio paquete 'mcp' (viejo, sin mcp.server.fastmcp) y rompe panel.py /
#  mcps_cli.py / websocket_bridge.py con un ModuleNotFoundError confuso.
#
#  Uso:   source ./activar.sh     (o:  . ./activar.sh)
#  Luego: python3 panel.py / python3 websocket_bridge.py / lo que sea
#
#  La primera vez crea el venv e instala requirements.txt; las siguientes
#  solo lo activa.
# ============================================================
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$AQUI/venv"

if [ ! -d "$VENV" ]; then
    echo "Creando entorno virtual en $VENV ..."
    python3 -m venv "$VENV" || { echo "No se pudo crear el venv"; return 1 2>/dev/null || exit 1; }
fi

source "$VENV/bin/activate"

# Si requirements.txt cambio desde la ultima instalacion, se nota por la
# marca de tiempo: mas simple que comparar hashes para un puñado de paquetes.
if [ "$AQUI/requirements.txt" -nt "$VENV/.instalado" ] 2>/dev/null || [ ! -f "$VENV/.instalado" ]; then
    echo "Instalando/actualizando dependencias..."
    pip install -q --upgrade pip
    pip install -q -r "$AQUI/requirements.txt" && touch "$VENV/.instalado"
fi

echo "Entorno del servidor listo · $(command -v python3) · mcp $(python3 -c 'import mcp; print(getattr(mcp, "__version__", "?"))' 2>/dev/null)"
