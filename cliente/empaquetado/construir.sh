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
rm -rf dist build && mkdir -p dist build/licencias

# Recursos que van dentro de todos los paquetes (descargados una vez, de
# versiones fijas): mapeado de mandos de SDL_GameControllerDB y los textos
# de licencia de las bibliotecas que trae pygame-ce (ver licencias.py).
PYGAME_CE=2.5.2
CONTROLLERDB=07af1d50187e94c861c9f9f5d8a55b148f11306b
bajar() { curl -fsSL --retry 3 -o "$2" "$1"; }
bajar "https://raw.githubusercontent.com/mdqinc/SDL_GameControllerDB/$CONTROLLERDB/gamecontrollerdb.txt" build/gamecontrollerdb.txt
bajar "https://raw.githubusercontent.com/mdqinc/SDL_GameControllerDB/$CONTROLLERDB/LICENSE" build/licencias/gamecontrollerdb-LICENSE.txt
bajar "https://raw.githubusercontent.com/pygame-community/pygame-ce/$PYGAME_CE/docs/LGPL.txt" build/licencias/pygame-ce-LGPL.txt
for f in FLAC fluidsynth freetype jpeg mpg123 ogg-vorbis opus opusfile png sdl2 sdl2_image sdl2_mixer tiff webp zlib; do
  bajar "https://raw.githubusercontent.com/pygame-community/pygame-ce/$PYGAME_CE/docs/licenses/LICENSE.$f.txt" "build/licencias/LICENSE.$f.txt"
done
bajar "https://raw.githubusercontent.com/libsdl-org/SDL_ttf/release-2.22.0/LICENSE.txt" build/licencias/SDL_ttf-LICENSE.txt
bajar "https://raw.githubusercontent.com/harfbuzz/harfbuzz/10.1.0/COPYING" build/licencias/harfbuzz-COPYING.txt
bajar "https://raw.githubusercontent.com/libsndfile/libsndfile/1.2.2/COPYING" build/licencias/libsndfile-COPYING.txt
bajar "https://raw.githubusercontent.com/dbry/WavPack/5.7.0/COPYING" build/licencias/wavpack-COPYING.txt
bajar "https://raw.githubusercontent.com/google/brotli/v1.1.0/LICENSE" build/licencias/brotli-LICENSE.txt

linux() {  # <plataforma docker> <arquitectura>
  docker run --rm --platform "$1" -v "$RAIZ":/src -w /src python:3.12-slim-bullseye bash -euc "
    apt-get -qq update >/dev/null && apt-get -qq install -y binutils fonts-dejavu-core >/dev/null
    pip install -q --disable-pip-version-check pyinstaller==6.11.1 pygame-ce==2.5.2 certifi
    mkdir -p /tmp/w/romslibrary/recursos && cp -r romslibrary/* /tmp/w/romslibrary/
    cp /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf build/gamecontrollerdb.txt /tmp/w/romslibrary/recursos/
    [ -f publico/LICENSE ] && cp publico/LICENSE /tmp/w/romslibrary/recursos/LICENSE
    python empaquetado/licencias.py /tmp/w/romslibrary/recursos/licencias build/licencias
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
  cp build/gamecontrollerdb.txt /tmp/w/romslibrary/recursos/
  [ -f publico/LICENSE ] && cp publico/LICENSE /tmp/w/romslibrary/recursos/LICENSE
  wine python empaquetado/licencias.py 'Z:\\tmp\\w\\romslibrary\\recursos\\licencias' 'Z:\\src\\build\\licencias' >/dev/null 2>&1 \
    || echo 'AVISO: licencias de Windows sin copiar'
  cp /usr/share/doc/fonts-dejavu-core/copyright /tmp/w/romslibrary/recursos/licencias/dejavu-copyright.txt 2>/dev/null \
    && printf 'dejavu-copyright.txt\tFuente DejaVu\n' >> /tmp/w/romslibrary/recursos/licencias/INDICE.txt || true
  cd /tmp/w && wine pyinstaller -y --noconfirm --noupx --log-level WARN --windowed --name romslibrary --onedir \
    --add-data 'romslibrary/recursos;romslibrary/recursos' romslibrary.py
  cd dist && python3 -m zipfile -c /src/dist/RomsLibrary-windows-x86_64.zip romslibrary 2>/dev/null \
    || zip -qr /src/dist/RomsLibrary-windows-x86_64.zip romslibrary
  chown -R $(stat -c %u:%g "$RAIZ") /src/dist"

# Se ejecuta con sudo: que lo generado siga siendo del usuario (publicar.sh lo borra sin sudo).
chown -R "$(stat -c %u:%g "$RAIZ")" build dist
ls -la dist
