#!/usr/bin/env bash
# Instalador idempotente del servidor (ola 8, operacion/tasks.md item 1).
#
# Se puede correr tantas veces como haga falta: cada paso comprueba su
# propio estado antes de actuar. Pensado para "clonar y correr" en un
# servidor nuevo, y para "volver a correr" en uno existente tras un
# `git pull` sin duplicar trabajo ni pisar secretos ya generados.
#
# Uso:
#   cd servidor/deploy && ./instalar.sh
#
# Variables opcionales:
#   SIN_SERVICIOS=1      no toca systemd (solo deja el venv y el .env listos)
#   TOKEN_OBLIGATORIO=1  genera HUD_TOKEN si falta (ola 8, tarea "HUD_TOKEN
#                        obligatorio en produccion"). Sin esto, el servidor
#                        sigue en modo abierto por defecto -- ver el porque
#                        en PROTOCOLO.md, seccion Autenticacion. Al ponerlo,
#                        aparte de este .env hay que grabar el MISMO valor
#                        en la NVS de cada cuerpo (bola y Stick) para que
#                        dejen de conectar sin token.
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVIDOR="$(dirname "$AQUI")"          # .../servidor
REPO="$(dirname "$SERVIDOR")"          # raiz del repo, ruta REAL de este servidor
cd "$SERVIDOR"

echo "==> Servidor: $SERVIDOR"

# ---------------------------------------------------------------
# 1. venv + dependencias
# ---------------------------------------------------------------
if [ ! -d venv ]; then
    echo "==> Creando venv"
    python3 -m venv venv
fi
source venv/bin/activate
echo "==> Instalando/actualizando requirements.txt"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# ---------------------------------------------------------------
# 2. .env: crear si no existe, generar tokens que falten (nunca pisa
#    uno ya puesto -- perder PANEL_TOKEN o HUD_TOKEN en produccion
#    desconectaria el panel o el dispositivo con token configurado).
# ---------------------------------------------------------------
touch .env
genera_si_falta() {
    local clave="$1"
    if ! grep -q "^${clave}=" .env 2>/dev/null; then
        local valor
        valor="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
        echo "${clave}=\"${valor}\"" >> .env
        echo "==> ${clave} generado (nuevo)"
    else
        echo "==> ${clave} ya existia, no se toca"
    fi
}
genera_si_falta PANEL_TOKEN
# HUD_TOKEN NO se genera por defecto: vacio = modo abierto es una decision
# explicita (ver PROTOCOLO.md, seccion Autenticacion). TOKEN_OBLIGATORIO=1
# lo genera -- pero generar el valor aqui es solo la mitad: cada cuerpo
# (bola, Stick) tiene que llevar el MISMO valor grabado en su NVS o se
# quedara sin poder conectar. No hay forma de automatizar esa segunda mitad
# desde este script (vive en el firmware, otra ola).
if [ "${TOKEN_OBLIGATORIO:-0}" = "1" ]; then
    genera_si_falta HUD_TOKEN
    echo "==> TOKEN_OBLIGATORIO=1: recuerda grabar el mismo HUD_TOKEN en la NVS de cada dispositivo"
else
    echo "==> HUD_TOKEN no generado (modo abierto). TOKEN_OBLIGATORIO=1 para exigirlo."
fi

# ---------------------------------------------------------------
# 3. Unidades systemd: ajustar la ruta de ejemplo a la ruta REAL de
#    este servidor antes de instalar. Idempotente: sed sobre el
#    archivo de origen del repo (deploy/*.service), nunca sobre el ya
#    instalado en /etc/systemd/system.
# ---------------------------------------------------------------
if [ "${SIN_SERVICIOS:-0}" != "1" ]; then
    RUTA_EJEMPLO="/home/ubuntu/esp32-hud-idf"
    if [ "$RUTA_EJEMPLO" != "$REPO" ]; then
        echo "==> Ajustando ruta de las unidades: $RUTA_EJEMPLO -> $REPO"
        sed -i "s|${RUTA_EJEMPLO}|${REPO}|g" \
            "$AQUI/agentic-voz.service" "$AQUI/agentic-panel.service"
    fi

    echo "==> Instalando unidades systemd"
    sudo cp "$AQUI/agentic-voz.service" "$AQUI/agentic-panel.service" /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now agentic-voz agentic-panel

    echo "==> Estado:"
    systemctl status agentic-voz agentic-panel --no-pager -l | head -40
else
    echo "==> SIN_SERVICIOS=1: no se tocan las unidades systemd"
fi

echo "==> Listo. Verificar salud: curl -s http://127.0.0.1:8766/api/salud"
