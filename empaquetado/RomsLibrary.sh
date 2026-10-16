#!/bin/bash
# RomsLibrary para consolas con PortMaster (muOS, Knulli, ROCKNIX...). Va en la
# carpeta de ports junto a la carpeta romslibrary/. Sin PortMaster también
# arranca (sin el mapeado de mando del aparato).

XDG_DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
for c in /opt/system/Tools/PortMaster /opt/tools/PortMaster "$XDG_DATA_HOME/PortMaster" /roms/ports/PortMaster; do
  if [ -f "$c/control.txt" ]; then controlfolder="$c"; break; fi
done
if [ -n "$controlfolder" ]; then
  source "$controlfolder/control.txt"
  [ -f "$controlfolder/mod_${CFW_NAME}.txt" ] && source "$controlfolder/mod_${CFW_NAME}.txt"
  get_controls
  export SDL_GAMECONTROLLERCONFIG="$sdl_controllerconfig"
fi

GAMEDIR="$(cd "$(dirname "$0")" && pwd)/romslibrary"
cd "$GAMEDIR" || exit 1
chmod +x ./romslibrary 2>/dev/null   # al descomprimir un .zip se pierde el permiso
./romslibrary > "$GAMEDIR/log.txt" 2>&1

[ -n "$controlfolder" ] && pm_finish
