#!/usr/bin/env bash
# Lanza todas las pruebas del servidor. Sin dependencias externas ni pytest.
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
fallos=0
for t in pruebas/test_*.py; do
    echo "── $t"
    python3 "$t" || fallos=$((fallos+1))
    echo
done
[ $fallos -eq 0 ] && echo "TODO VERDE" || echo "$fallos suite(s) con fallos"
exit $fallos
