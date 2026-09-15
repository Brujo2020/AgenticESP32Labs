#!/usr/bin/env bash
# Prueba de restauracion de un respaldo de respaldo.sh (ola 8, item 7: "prueba
# de restauracion DOCUMENTADA" -- un respaldo que nunca se restauro no es un
# respaldo, es una esperanza).
#
# Por defecto SOLO verifica (descifra + extrae a una carpeta temporal y
# lista el contenido) sin tocar el servidor real. Con --aplicar, copia los
# archivos verificados sobre el servidor (pide confirmacion).
#
# Uso:
#   ./restaurar.sh /ruta/agentic-respaldo-XXXX.tar.gz.gpg
#   ./restaurar.sh /ruta/agentic-respaldo-XXXX.tar.gz.gpg --aplicar
set -euo pipefail

ARCHIVO="${1:?uso: restaurar.sh <archivo.tar.gz.gpg> [--aplicar]}"
APLICAR="${2:-}"
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVIDOR="$(dirname "$AQUI")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> Descifrando $ARCHIVO"
gpg --decrypt -o "$TMP/respaldo.tar.gz" "$ARCHIVO"

echo "==> Extrayendo"
tar -xzf "$TMP/respaldo.tar.gz" -C "$TMP"

echo "==> Contenido verificado:"
find "$TMP" -maxdepth 1 -type f ! -name 'respaldo.tar.gz' -exec ls -la {} \;

if [ "$APLICAR" = "--aplicar" ]; then
    read -r -p "Esto SOBREESCRIBE .env/ajustes.yaml en $SERVIDOR. ¿Seguro? (si/no) " ok
    if [ "$ok" = "si" ]; then
        cp -v "$TMP"/.env "$TMP"/ajustes.yaml "$SERVIDOR"/ 2>/dev/null || true
        [ -f "$TMP/config.yaml" ] && cp -v "$TMP/config.yaml" "$SERVIDOR/"
        echo "==> Restaurado. Reiniciar servicios: sudo systemctl restart agentic-voz agentic-panel"
    else
        echo "==> Cancelado, nada se toco"
    fi
else
    echo "==> Solo verificacion (nada se toco). Repetir con --aplicar para restaurar de verdad."
fi
