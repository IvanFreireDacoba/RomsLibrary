#!/bin/bash
# Empaqueta la app (PyInstaller, carpeta "romslibrary/") para las tres
# plataformas, en Docker. Necesita Docker y qemu-user-binfmt (para
# ARM64). Resultado en ./dist:
#   RomsLibrary-linux-x86_64.tar.gz    Steam Deck / PC Linux (instalar y actualizar)
#   RomsLibrary-linux-aarch64.tar.gz   consolas ARM (actualizar)
#   RomsLibrary-PortMaster.zip         consolas ARM (instalar: RomsLibrary.sh + romslibrary/)
#   RomsLibrary-windows-x86_64.zip     Windows / ROG Ally (instalar y actualizar)
# Bullseye (glibc 2.31): el binario arranca en sistemas más viejos que el de la consola.
set -euo pipefail
cd "$(dirname "$0")/.."
RAIZ=$(pwd)
rm -rf dist build && mkdir -p dist

linux() {  # <plataforma docker> <arquitectura>
  docker run --rm --platform "$1" -v "$RAIZ":/src -w /src python:3.12-slim-bullseye bash -euc "
    apt-get -qq update >/dev/null && apt-get -qq install -y binutils fonts-dejavu-core >/dev/null
    pip install -q --disable-pip-version-check pyinstaller==6.11.1 pygame-ce==2.5.2 certifi
    mkdir -p /tmp/w/romslibrary/recursos && cp -r romslibrary/* /tmp/w/romslibrary/
    cp /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf /tmp/w/romslibrary/recursos/
    cp romslibrary.py /tmp/w/
    cd /tmp/w && pyinstaller -y --noconfirm --noupx --log-level WARN --name romslibrary --onedir \
      --add-data romslibrary/recursos:romslibrary/recursos romslibrary.py
    tar czf /src/dist/RomsLibrary-linux-$2.tar.gz -C dist romslibrary
    if [ $2 = aarch64 ]; then
      cp /src/empaquetado/RomsLibrary.sh dist/ && chmod +x dist/RomsLibrary.sh dist/romslibrary/romslibrary
      cd dist && python -m zipfile -c /src/dist/RomsLibrary-PortMaster.zip RomsLibrary.sh romslibrary
    fi
    chown -R $(stat -c %u:%g "$RAIZ") /src/dist"
}

linux linux/amd64 x86_64
linux linux/arm64 aarch64

# Windows: Python de Windows dentro de Wine.
docker run --rm -v "$RAIZ":/src -w /src tobix/pywine:3.12 bash -euc "
  wine python -m pip install -q --disable-pip-version-check pyinstaller==6.11.1 pygame-ce==2.5.2 certifi >/dev/null 2>&1
  apt-get -qq update >/dev/null 2>&1 && apt-get -qq install -y fonts-dejavu-core zip >/dev/null 2>&1 || true
  mkdir -p /tmp/w/romslibrary/recursos && cp -r romslibrary/* /tmp/w/romslibrary/ && cp romslibrary.py /tmp/w/
  cp /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf /tmp/w/romslibrary/recursos/ 2>/dev/null || true
  cd /tmp/w && wine pyinstaller -y --noconfirm --noupx --log-level WARN --windowed --name romslibrary --onedir \
    --add-data 'romslibrary/recursos;romslibrary/recursos' romslibrary.py
  cd dist && python3 -m zipfile -c /src/dist/RomsLibrary-windows-x86_64.zip romslibrary 2>/dev/null \
    || zip -qr /src/dist/RomsLibrary-windows-x86_64.zip romslibrary
  chown -R $(stat -c %u:%g "$RAIZ") /src/dist"

ls -la dist
