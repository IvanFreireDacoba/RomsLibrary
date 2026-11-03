#!/bin/bash
# Lanza las pruebas del servicio dentro de su imagen Docker (en jarjar):
#   servicio/pruebas/lanzar.sh [imagen]
set -euo pipefail
SERVICIO="$(cd "$(dirname "$0")/.." && pwd)"
sudo docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$SERVICIO":/servicio:ro "${1:-romslibrary:latest}" \
    sh -c 'pip install -q --user pytest 2>/dev/null; cd /tmp && python -m pytest -q -p no:cacheprovider /servicio/pruebas'
