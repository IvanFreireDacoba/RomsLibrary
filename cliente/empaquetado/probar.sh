#!/bin/bash
# Pruebas del paquete ARM64 (PortMaster) sin consola, en jarjar: Docker +
# qemu, SDL en modo «dummy», acciones guionizadas (RL_GUION) y capturas
# (RL_CAPTURAS) a 640x480 (RG35XX SP) y 720x480 (RG34XX). Simula los árboles
# de carpetas de Knulli, muOS y un Linux ARM desconocido, e instala un juego
# pequeño de Game Boy en cada uno.
#
#   sudo -n bash empaquetado/probar.sh [dist/RomsLibrary-PortMaster.zip]
#
# Nunca crea consolas reales: ajustes.json lleva un identificador falso y el
# servicio de partidas apunta a un puerto cerrado. La librería es la local
# (100.124.117.73:8766) con su clave, que solo se escribe en el ajustes.json
# temporal y se borra al acabar.
set -euo pipefail
cd "$(dirname "$0")/.."
RAIZ=$(pwd)
ZIP=${1:-dist/RomsLibrary-PortMaster.zip}
SALIDA=$RAIZ/pruebas
rm -rf "$SALIDA" && mkdir -p "$SALIDA"
CLAVE=$(docker exec romslibrary python -c "import ajustes;print(ajustes.leer('clave_app'))")

# Menú: 0 partidas, 1 consolas, 2 ROMs, 3 librería, 4 ajustes, 5 Versión, 6 salir.
GUION_COMPLETO="tabla; esperar 3; captura lista; pag_sig; tabla; esperar 2; captura gb; aceptar; esperar 10; captura instalado;
menu; abajo; abajo; abajo; abajo; abajo; captura menu; aceptar; esperar 1; captura version;
aceptar; esperar 6; captura aviso; derecha; captura aviso2; atras;
abajo; aceptar; esperar 1; captura licencia; atras;
abajo; aceptar; esperar 1; captura terceros; aceptar; esperar 1; captura tercero1; atras; atras; atras; salir"

prueba() {  # <nombre> <tamaño> <preparación en el contenedor> <guion>
  local nombre=$1 tam=$2 prep=$3 guion=$4
  local dir=$SALIDA/$nombre
  mkdir -p "$dir/capturas"
  cat > "$dir/ajustes.json" <<EOF
{"credencial": "pc-prueba-no-real", "url": "http://100.124.117.73:8766", "clave": "$CLAVE",
 "url_partidas": "http://127.0.0.1:9", "identificador": "PRUEB-00000", "nombre_consola": "Prueba ($nombre)",
 "token_partidas": "falso", "carpetas": {}, "todas_las_consolas": false, "vista": "lista", "accesos_pendientes": {}}
EOF
  docker run --rm --platform linux/arm64 -v "$RAIZ/$ZIP":/paquete.zip:ro -v "$dir":/salida \
    -e RL_TAMANO="$tam" -e RL_GUION="$guion" -e RL_CAPTURAS=/salida/capturas -e SDL_VIDEODRIVER=dummy \
    -e SDL_AUDIODRIVER=dummy python:3.12-slim-bullseye bash -euc "
      $prep
      mkdir -p /root/.config/romslibrary /ports && cp /salida/ajustes.json /root/.config/romslibrary/
      cd /ports && python -m zipfile -e /paquete.zip . && chmod +x RomsLibrary.sh
      ldd --version | head -1
      timeout 300 ./RomsLibrary.sh || echo \"salida \$?\"
      cp romslibrary/log.txt /salida/log.txt
      echo '--- ROMs tras la prueba:'; find /userdata/roms /mnt/mmc/ROMS /root/ROMs -type f 2>/dev/null | head -20 || true
      chown -R $(stat -c %u:%g "$RAIZ") /salida" > "$dir/salida.txt" 2>&1 || true
  rm -f "$dir/ajustes.json"
  echo "== $nombre ($tam)"; grep -E "fases|guion|ROMs tras|^/|GLIBC|glibc|Error|Traceback" "$dir/log.txt" "$dir/salida.txt" | head -30
}

prueba knulli-640x480 640x480 "mkdir -p /userdata/roms/gb /userdata/saves; echo 'NAME=knulli' > /etc/os-release" "$GUION_COMPLETO"
prueba knulli-720x480 720x480 "mkdir -p /userdata/roms/gb /userdata/saves; echo 'NAME=knulli' > /etc/os-release" "$GUION_COMPLETO"
prueba muos-640x480 640x480 "mkdir -p /opt/muos '/mnt/mmc/ROMS/Nintendo Game Boy' /mnt/mmc/MUOS/retroarch" \
  "tabla; esperar 3; captura lista; pag_sig; tabla; esperar 2; aceptar; esperar 10; captura instalado; salir"
prueba desconocido-640x480 640x480 "true" "esperar 3; captura carpeta; atras; salir"
# Sin SDL_VIDEODRIVER ni DISPLAY (como en la consola): intenta KMS/DRM y, si no hay, lo que SDL encuentre.
# (también con identidad falsa y sin servidores reales: url y partidas a un puerto cerrado).
docker run --rm --platform linux/arm64 -v "$RAIZ/$ZIP":/paquete.zip:ro python:3.12-slim-bullseye bash -c "
  mkdir -p /root/.config/romslibrary && echo '{\"credencial\": \"pc-prueba-no-real\", \"url\": \"http://127.0.0.1:9\",
    \"url_partidas\": \"http://127.0.0.1:9\", \"identificador\": \"PRUEB-00000\", \"nombre_consola\": \"Prueba\",
    \"token_partidas\": \"falso\"}' > /root/.config/romslibrary/ajustes.json
  mkdir -p /p && cd /p && python -m zipfile -e /paquete.zip . && chmod +x romslibrary/romslibrary
  cd romslibrary && timeout 60 ./romslibrary; echo \"salida \$?\"; cat /root/.config/romslibrary/error.log 2>/dev/null | tail -3" \
  > "$SALIDA/kmsdrm.txt" 2>&1 || true
echo "== sin vídeo forzado:"; tail -5 "$SALIDA/kmsdrm.txt"
chown -R "$(stat -c %u:%g "$RAIZ")" "$SALIDA"
