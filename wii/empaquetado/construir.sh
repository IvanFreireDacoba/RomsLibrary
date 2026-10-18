#!/bin/bash
# Compila la app de Wii en jarjar con Docker (devkitpro/devkitppc) y deja en
# dist/ la carpeta apps/romslibrary y RomsLibrary-Wii.zip.
#   sudo bash empaquetado/construir.sh          # Wii
#   sudo bash empaquetado/construir.sh linux    # versión de PC para probar
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "${1:-}" = linux ]; then
  docker build -q -t romslibrary-wii-linux -f empaquetado/Dockerfile.linux empaquetado >/dev/null
  docker run --rm -v "$PWD":/src -w /src romslibrary-wii-linux bash -c \
    'test -d build/mbedtls || { echo "Primero compila la de Wii (descarga mbedTLS)"; exit 1; }; make -j"$(nproc)" linux'
else
  docker run --rm -v "$PWD":/src -w /src devkitpro/devkitppc bash empaquetado/compilar.sh
fi
chown -R "${SUDO_UID:-1000}:${SUDO_GID:-1000}" build dist cache 2>/dev/null || true
