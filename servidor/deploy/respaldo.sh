#!/usr/bin/env bash
# Respaldo cifrado del estado que NO esta en git (ola 8, operacion/tasks.md
# item 7): .env (tokens y claves de proveedores), ajustes.yaml (toggles del
# panel), config.yaml si difiere del versionado. Todo lo demas (codigo) ya
# esta a salvo en GitHub -- esto es solo lo que un `git clone` no reconstruye.
#
# Cifrado simetrico con gpg (AES256): la clave la pones tu, nunca queda en
# disco ni en este script. Sin la frase de paso, el .tar.gz.gpg es basura.
#
# Uso:
#   RESPALDO_PASS="frase-larga-que-solo-tu-sabes" ./respaldo.sh
#   (o deja que lo pida interactivo si no exportas la variable)
#
# Restaurar / verificar que el respaldo sirve: ./restaurar.sh <archivo.tar.gz.gpg>
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVIDOR="$(dirname "$AQUI")"
DESTINO="${DESTINO:-$HOME/respaldos-agentic}"
FECHA="$(date +%Y%m%d-%H%M%S)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$DESTINO"
cd "$SERVIDOR"

echo "==> Empaquetando estado (no-codigo) de $SERVIDOR"
tar -czf "$TMP/respaldo.tar.gz" \
    .env \
    ajustes.yaml \
    $( [ -f config.yaml ] && echo config.yaml ) \
    2>/dev/null || true

if [ ! -s "$TMP/respaldo.tar.gz" ]; then
    echo "ERROR: no se genero el tar (¿faltan .env/ajustes.yaml?)" >&2
    exit 1
fi

ARCHIVO="$DESTINO/agentic-respaldo-$FECHA.tar.gz.gpg"
if [ -n "${RESPALDO_PASS:-}" ]; then
    gpg --batch --yes --passphrase "$RESPALDO_PASS" --symmetric --cipher-algo AES256 \
        -o "$ARCHIVO" "$TMP/respaldo.tar.gz"
else
    gpg --symmetric --cipher-algo AES256 -o "$ARCHIVO" "$TMP/respaldo.tar.gz"
fi

echo "==> Respaldo cifrado: $ARCHIVO"

# Rotacion simple: conservar solo los ultimos 14 respaldos.
ls -1t "$DESTINO"/agentic-respaldo-*.tar.gz.gpg 2>/dev/null | tail -n +15 | xargs -r rm -f
echo "==> Respaldos conservados: $(ls -1 "$DESTINO"/agentic-respaldo-*.tar.gz.gpg 2>/dev/null | wc -l)"
