# RomsLibrary para consolas Linux, Steam Deck y Windows

La misma librería que la app de Android (catálogo, idiomas, aviso de archivo
corrupto, enlaces de autores, versiones de los ROMs y partidas), con una
interfaz para mando. Python + pygame-ce, empaquetado con PyInstaller.

| Dónde | Paquete | Cómo se instala |
|---|---|---|
| muOS, Knulli, ROCKNIX… (ARM64) | `RomsLibrary-PortMaster.zip` | Descomprimir en la carpeta de *ports*; aparece en *Ports* |
| Steam Deck / PC Linux | `RomsLibrary-linux-x86_64.tar.gz` | Descomprimir; `romslibrary/romslibrary` (en la Deck, como juego no-Steam) |
| Windows / ROG Ally | `RomsLibrary-windows-x86_64.zip` | Descomprimir; `romslibrary\romslibrary.exe` |

Descargas: `https://sund.es/APIs/RomLibraryApp?accion=cliente&paquete=<linux-aarch64|linux-x86_64|windows-x86_64>-instalar`.
Después se actualiza sola (`autoactualizar.py`, siempre desde sund.es).

## Controles

A instalar/desinstalar · Y elegir idioma · X buscar · L/R consola ·
Select filtro (instalados) · Start menú (partidas, ROMs, ajustes) · B salir.
En PC también teclado (flechas, Intro, Esc, Tab, Y, X, Re Pág/Av Pág).

## Carpetas (`plataforma.py`)

Al arrancar detecta el sistema y propone la carpeta ROMs; todo se cambia en
*Ajustes* (también el nombre de la carpeta de cada consola).

| Sistema | ROMs | Carpetas de consola | Partidas (RetroArch) |
|---|---|---|---|
| Knulli / Batocera | `/userdata/roms` | ES-DE, `gamecube` | `/userdata/saves/<consola>/` |
| muOS | `/mnt/sdcard/ROMS` o `/mnt/mmc/ROMS` | la que ya exista para esa consola (alias) o la de ES-DE | `retroarch.cfg` de muOS (por núcleo) |
| ROCKNIX | `/storage/roms` | ES-DE, `gamecube` | `retroarch.cfg` |
| EmuDeck / ES-DE / Linux / Windows | `~/Emulation/roms`, la de ES-DE o `~/ROMs` | ES-DE | `retroarch.cfg` |

- En ARM solo se ofrecen las consolas que mueve un portátil (GB, GBC, GBA, N64,
  PS1, PSP); *Ajustes → Mostrar todas las consolas* las enseña todas.
- Juegos de varios ficheros: con ES-DE, carpeta `Juego.cue/` (un juego); en el
  resto, carpeta `Juego/`.
- RPG Maker solo en Windows (Game.exe).

## Partidas (`guardados.py`)

Mismo formato que Android (`sistema/clave/<saves|states>__<núcleo>__<fichero>`),
así que pasan de una Retroid a una consola Linux o a la Deck. Gana la copia
más reciente y al bajar se conserva la fecha. Donde RetroArch no separa por
núcleo (Knulli), se usa el núcleo habitual de cada consola
(`NUCLEO_POR_DEFECTO`). Fuera de RetroArch (PCSX2, Dolphin...) aún no.

La consola se registra sola en el servidor la primera vez, con una
credencial aleatoria guardada en `~/.config/romslibrary/ajustes.json`
(`%APPDATA%\RomsLibrary` en Windows).

## Desarrollo

    pip install pygame-ce certifi
    python romslibrary.py --ventana          # desde el código
    empaquetado/construir.sh                 # paquetes de las tres plataformas en ./dist (Docker;
                                             # ARM64 necesita qemu-user-binfmt)

Los paquetes oficiales se publican en el servicio de sund.es y también en las
*Releases* de este repo.

## Repos relacionados

- [RomsLibrary-Servicio](https://github.com/IvanFreireDacoba/RomsLibrary-Servicio): el servicio (API y gestor web).
- [RomsLibrary-App](https://github.com/IvanFreireDacoba/RomsLibrary-App): la app de Android.

## Licencia

MIT. Ver [LICENSE](LICENSE).
