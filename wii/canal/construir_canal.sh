#!/bin/bash
# Monta el canal de RomsLibrary (RomsLibrary-canal.wad) en jarjar:
#   sudo bash canal/construir_canal.sh        (desde la carpeta wii/)
# - Contenido 1: el cargador (cargador/), que abre apps/romslibrary/boot.dol de la
#   SD o del USB; la app se sigue actualizando sola, el canal no cambia.
# - Contenido 0: banner, icono y sonido (banner/), hechos con las herramientas del
#   Homebrew Channel (GPLv2), que se descargan aquí en una versión fija.
# - Firma «falsa» (trucha), como cualquier canal homebrew: se instala con un
#   gestor de WAD que use un cIOS (p. ej. YAWM ModMii Edition con d2x).
# Hace falta la clave común de la Wii en ~/.secrets/wii/common-key (no va en el repo).
set -euo pipefail
cd "$(dirname "$0")"
HBC_COMMIT=13305cfbe5db47eebc5e62aeed14f7adfbb83a5a
TITULO=524d4c42                  # «RMLB»: 00010001-524D4C42
VERSION=$(sed -n 's/.*RL_CANAL "\(.*\)".*/\1/p' version_canal.h)
CLAVE=${SUDO_USER:+/home/$SUDO_USER}/.secrets/wii/common-key
[ -f "$CLAVE" ] || { echo "Falta $CLAVE"; exit 1; }

docker run --rm -v "$PWD/cargador":/src -w /src devkitpro/devkitppc bash -c \
  'export DEVKITPRO=/opt/devkitpro DEVKITPPC=/opt/devkitpro/devkitPPC PATH=/opt/devkitpro/devkitPPC/bin:/opt/devkitpro/tools/bin:$PATH; make -s'

docker build -q -t romslibrary-canal -f Dockerfile.canal . >/dev/null
docker run --rm -v "$PWD":/canal -v "$CLAVE":/root/.wii/common-key:ro -w /canal \
  -e HBC_COMMIT=$HBC_COMMIT -e TITULO=$TITULO -e VERSION="$VERSION" romslibrary-canal bash -c '
set -euo pipefail
mkdir -p cache build
if [ ! -d cache/hbc ]; then
  git clone -q https://github.com/fail0verflow/hbc.git cache/hbc
fi
git -C cache/hbc checkout -q $HBC_COMMIT
T=cache/hbc/channel/banner/tools
make -s -C $T png2tpl lz77 mkbns >/dev/null
export ALAMEDA=$PWD/cache/hbc/pywii/Alameda
PYWII=cache/hbc/pywii/pywii-tools
rm -rf build && mkdir -p build/data/meta
seccion() {   # icon | banner
  local d=build/$1
  mkdir -p $d/arc/blyt $d/arc/anim $d/arc/timg
  local pngs="banner/fondo.png banner/banner_*.png"
  [ $1 = icon ] && pngs="banner/fondo.png banner/icono_*.png"
  for png in $pngs; do
    $T/png2tpl $png $d/arc/timg/$(basename $png .png).tpl 0 0 >/dev/null
  done
  if [ $1 = icon ]; then
    (cd banner && python mkicono.py ../$d/arc/blyt/icon.brlyt ../$d/arc/anim/icon.brlan) >/dev/null
  else
    (cd banner && python mkbanner.py ../$d/arc/blyt/banner.brlyt ../$d/arc/anim/banner_Start.brlan ../$d/arc/anim/banner_Loop.brlan) >/dev/null
  fi
  python $PYWII/arcpack.py build/$1.arc $d >/dev/null
  $T/lz77 build/$1.arc build/$1.lz77 >/dev/null
  python $T/addimd5.py build/$1.lz77 build/data/meta/$1.bin
}
seccion icon
seccion banner
python banner/sonido.py build/sonido.raw
$T/mkbns build/sonido.raw build/sound.bns >/dev/null
python $T/addimd5.py build/sound.bns build/data/meta/sound.bin
python $PYWII/arcpack.py build/data.arc build/data >/dev/null
python $T/join-imet.py build/canal.imet build/data.arc build/icon.arc build/banner.arc build/sound.bns banner/nombres.txt

# WAD: contenido 0 = pie (0x40) + banner; contenido 1 = cargador.
W=build/wad && mkdir -p $W
python -c "import sys; sys.stdout.write((\"RomsLibrary \" + \"$VERSION\").ljust(64, chr(0)))" > $W/footer
cat $W/footer build/canal.imet > $W/00000000
cp cargador/cargador.dol $W/00000001
cp cache/hbc/channel/title/retail/certs $W/certs
python - <<PY
import binascii
tid = binascii.unhexlify("00010001" + "$TITULO")
tmd = bytearray(open("cache/hbc/channel/title/tmd.template", "rb").read())
tmd[0x18C:0x194] = tid
open("$W/tmd", "wb").write(tmd)
tik = bytearray(open("cache/hbc/channel/title/cetk.template", "rb").read())
tik[0x1DC:0x1E4] = tid
open("$W/cetk", "wb").write(tik)
PY
M=$(echo $VERSION | cut -d. -f1); N=$(echo $VERSION | cut -d. -f2)
(cd $PYWII && python tikfix.py /canal/$W/cetk && python tmdupdatecr.py /canal/$W/tmd /canal/$W \
   && python tmdvers.py /canal/$W/tmd $M $N && python wadpack.py /canal/build/RomsLibrary-canal.wad /canal/$W) >/dev/null
python $PYWII/wadinfo.py build/RomsLibrary-canal.wad 2>&1 | head -40
'
mkdir -p ../dist && cp build/RomsLibrary-canal.wad ../dist/
chown -R "${SUDO_UID:-1000}:${SUDO_GID:-1000}" build cache cargador ../dist 2>/dev/null || true
ls -la ../dist/RomsLibrary-canal.wad
