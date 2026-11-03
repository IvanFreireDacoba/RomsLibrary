#!/bin/bash
# RomsLibrary para consolas con PortMaster (Knulli, muOS/MustardOS, ROCKNIX...;
# probado para Anbernic H700: RG35XX SP, RG34XX). Va en la carpeta de ports
# junto a la carpeta romslibrary/ (Knulli: /userdata/roms/ports/). Lanzador
# estándar de PortMaster: control.txt da el mapeado del mando del aparato
# (SDL_GAMECONTROLLERCONFIG). La app lee el mando con SDL GameController, así
# que no hace falta gptokeyb. Sin PortMaster también arranca.

XDG_DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}

if [ -d "/opt/system/Tools/PortMaster/" ]; then
  controlfolder="/opt/system/Tools/PortMaster"
elif [ -d "/opt/tools/PortMaster/" ]; then
  controlfolder="/opt/tools/PortMaster"
elif [ -d "$XDG_DATA_HOME/PortMaster/" ]; then
  controlfolder="$XDG_DATA_HOME/PortMaster"
else
  controlfolder="/roms/ports/PortMaster"
fi

if [ -f "$controlfolder/control.txt" ]; then
  source "$controlfolder/control.txt"
  [ -f "${controlfolder}/mod_${CFW_NAME}.txt" ] && source "${controlfolder}/mod_${CFW_NAME}.txt"
  get_controls
  export SDL_GAMECONTROLLERCONFIG="$sdl_controllerconfig"
else
  controlfolder=""
fi

GAMEDIR="$(cd "$(dirname "$0")" && pwd)/romslibrary"
cd "$GAMEDIR" || exit 1
# Al descomprimir un .zip se pierde el permiso de ejecución.
$ESUDO chmod +x ./romslibrary 2>/dev/null || chmod +x ./romslibrary 2>/dev/null
# Ajustes y caché junto a la app si la carpeta personal no se puede escribir
# (la app ya lo resuelve sola; ver ajustes.carpeta_datos).
export HOME="${HOME:-$GAMEDIR}"
# Sin X11 ni Wayland la app usa KMS/DRM por su cuenta (ui/app.py).
[ -n "$controlfolder" ] && command -v pm_platform_helper >/dev/null 2>&1 && pm_platform_helper "$GAMEDIR/romslibrary"

./romslibrary > "$GAMEDIR/log.txt" 2>&1

[ -n "$controlfolder" ] && command -v pm_finish >/dev/null 2>&1 && pm_finish
