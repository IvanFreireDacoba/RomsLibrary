#!/bin/bash
# Dentro del contenedor devkitpro/devkitppc (lo lanza construir.sh): compila
# boot.dol y deja en dist/ la carpeta apps/romslibrary y el zip de instalación.
set -euo pipefail
cd /src
MBEDTLS_VERSION=3.6.7
MBEDTLS_SHA256=a7e8bcbec0e6f761b4af24f25677626b35f762f68eef79c08677a363212d11f6
if [ ! -d build/mbedtls ]; then
  mkdir -p build cache
  [ -f cache/mbedtls-$MBEDTLS_VERSION.tar.bz2 ] || curl -fsSL -o cache/mbedtls-$MBEDTLS_VERSION.tar.bz2 \
    https://github.com/Mbed-TLS/mbedtls/releases/download/mbedtls-$MBEDTLS_VERSION/mbedtls-$MBEDTLS_VERSION.tar.bz2
  echo "$MBEDTLS_SHA256  cache/mbedtls-$MBEDTLS_VERSION.tar.bz2" | sha256sum -c -
  tar xjf cache/mbedtls-$MBEDTLS_VERSION.tar.bz2 -C build
  mv build/mbedtls-$MBEDTLS_VERSION build/mbedtls
fi
export DEVKITPRO=/opt/devkitpro DEVKITPPC=/opt/devkitpro/devkitPPC
export PATH=$DEVKITPPC/bin:$DEVKITPRO/tools/bin:$PATH
make -j"$(nproc)" wii

VERSION=$(sed -n 's/.*RL_VERSION "\(.*\)".*/\1/p' fuente/version.h)
APP=dist/apps/romslibrary
rm -rf dist && mkdir -p $APP
cp build/boot.dol recursos/icon.png $APP/
sed -e "s/@VERSION@/$VERSION/" -e "s/@FECHA@/$(date +%Y%m%d)/" recursos/meta.xml.in > $APP/meta.xml

# Licencias de todo lo que va dentro de boot.dol.
L=$APP/LICENCIAS.txt
{
  echo "RomsLibrary para Wii $VERSION — avisos de licencia"
  echo "================================================"
  echo
  cat recursos/LICENCIAS-propias.txt
  seccion() { echo; echo "----------------------------------------------------------------"; echo "$1"; echo "----------------------------------------------------------------"; cat "${@:2}"; }
  seccion "mbedTLS $MBEDTLS_VERSION (Apache-2.0)" build/mbedtls/LICENSE
  seccion "SDL2 (zlib)" /opt/devkitpro/portlibs/gamecube/share/licenses/SDL2/LICENSE.txt
  seccion "SDL2_ttf (zlib)" /opt/devkitpro/portlibs/wiiu/licenses/wiiu-sdl2_ttf/COPYING.txt
  seccion "SDL2_image (zlib)" /opt/devkitpro/portlibs/wiiu/licenses/wiiu-sdl2_image/LICENSE.txt
  for p in freetype harfbuzz libpng libjpeg-turbo zlib bzip2 brotli jansson; do
    for f in /opt/devkitpro/portlibs/ppc/licenses/ppc-$p/*; do
      case "$f" in *GPLv2*) continue;; esac          # FreeType se usa bajo la FTL
      seccion "$p — $(basename "$f")" "$f"
    done
  done
  seccion "libogc" /opt/devkitpro/libogc/LICENSE
} > $L

( cd dist && python3 - <<PY
import zipfile, os
with zipfile.ZipFile("RomsLibrary-Wii.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for raiz, _, ficheros in os.walk("apps"):
        for f in sorted(ficheros):
            z.write(os.path.join(raiz, f))
PY
)
ls -la $APP dist
