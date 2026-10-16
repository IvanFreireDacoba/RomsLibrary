# RomsLibrary — App para consolas Linux, Steam Deck y Windows

La misma app que [RomsLibrary-App](https://github.com/IvanFreireDacoba/RomsLibrary-App)
(Android), para el resto de sistemas: consolas Linux con PortMaster (muOS,
Knulli, ROCKNIX…), Steam Deck, PC Linux y Windows (también la ROG Xbox Ally).
Se conecta a una librería [RomsLibrary](https://github.com/IvanFreireDacoba/RomsLibrary-Servicio),
instala los juegos en tu carpeta de ROMs y sincroniza las partidas de
RetroArch entre tus consolas, también con las Android. Se maneja con el mando.

## Descargar

Los paquetes están en **[Releases](https://github.com/IvanFreireDacoba/RomsLibrary-Cliente/releases/latest)**.
Una vez instalada se actualiza sola.

| Dónde | Paquete | Cómo se instala |
|---|---|---|
| muOS, Knulli, ROCKNIX… (ARM64) | `RomsLibrary-PortMaster.zip` | Descomprimir en la carpeta de *ports*; aparece en *Ports* |
| Steam Deck / PC Linux | `RomsLibrary-linux-x86_64.tar.gz` | Descomprimir y abrir `romslibrary/romslibrary` (en la Deck, añadirlo como juego no-Steam) |
| Windows / ROG Ally | `RomsLibrary-windows-x86_64.zip` | Descomprimir y abrir `romslibrary\romslibrary.exe` |

## Uso

En *Start → Ajustes*:

- **Servidor:** viene `https://sund.es` (librería pública, sin clave). Puedes
  poner cualquier otra librería RomsLibrary.
- **Clave de acceso:** solo para librerías privadas (se ve en su gestor web).
- **Nombre de la consola:** con él la reconocerán las consolas con las que
  sincronices partidas.
- **Carpeta de ROMs:** la propone según el sistema; se puede cambiar, también
  la carpeta de cada consola.

Como en Android: una entrada por juego que instala la versión en español o,
si no hay, en inglés (Y para elegir otro idioma); aviso si el archivo está
marcado como corrupto; los juegos que su autor publica en su web se bajan de
su enlace con el SHA comprobado; y si la librería sube una versión nueva de
un juego instalado, la app la pone en lugar de la vieja con el mismo nombre
después de subir tus partidas.

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

La consola se registra sola en la librería la primera vez, con una
credencial aleatoria guardada en `~/.config/romslibrary/ajustes.json`
(`%APPDATA%\RomsLibrary` en Windows).


## Compilar

    pip install pygame-ce certifi
    python romslibrary.py --ventana          # desde el código
    empaquetado/construir.sh                 # paquetes de las tres plataformas en ./dist
                                             # (Docker; ARM64 necesita qemu-user-binfmt)

Python + pygame-ce, empaquetado con PyInstaller.

## Aviso legal

- RomsLibrary es software para gestionar **tu propia colección**. No incluye
  juegos, BIOS ni firmwares, no enlaza a ellos y no dice dónde conseguirlos.
- Cada persona es responsable de lo que guarda y comparte con él. Usa solo
  juegos que tengas derecho a usar y, si tu librería es pública, a distribuir:
  tus propias copias de seguridad, homebrew o juegos de licencia libre. En
  muchos países, incluida España, distribuir copias de juegos comerciales sin
  permiso es ilegal aunque sea gratis.
- La app solo descarga lo que ofrece la librería a la que la conectes, o las
  otras librerías públicas que esta te enseñe (en ese caso, directamente de
  ellas). Quien gestiona cada librería es responsable de su contenido.
- muOS, Knulli, ROCKNIX, PortMaster, ES-DE, RetroArch, Steam, Windows, ROG y
  los demás nombres citados son marcas de sus dueños. Este es un proyecto
  independiente, sin relación con ellos.
- Aviso legal, retirada de contenidos y privacidad de los servicios que
  funcionan en sund.es (app oficial, librería «SunD» e índice):
  https://sund.es/romslibrary/aviso-legal.
- Si crees que algo publicado con este proyecto vulnera tus derechos,
  escribe a `support@sund.es` o abre un issue en este repositorio.

## Licencia

[MIT](LICENSE): puedes usar, copiar, modificar y redistribuir el código como
quieras, también en proyectos comerciales; solo hay que conservar el aviso de
copyright y la licencia.

No es obligatorio, pero si lo usas o lo modificas se agradece un enlace a este
repositorio (https://github.com/IvanFreireDacoba/RomsLibrary-Cliente) para que otros puedan
encontrar el original.
