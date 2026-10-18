# RomsLibrary para Wii

La librería de RomsLibrary en la Wii, desde el Homebrew Channel y con el mando:

- **Juegos de Wii** (.wbfs, .iso; un .wbfs de más de 4 GB se parte en
  .wbf1, .wbf2...) → `usb:/wbfs/<Título> [ID]/`, donde los busca **USB Loader GX**.
- **Juegos de GameCube** (.iso/.gcm) → `usb:/games/<Título> [ID]/game.iso`, para
  **Nintendont** (USB Loader GX también los lanza).
- **Game Boy / Color / Advance** → `/vbagx/roms/` (Visual Boy Advance GX),
  **N64** → `/not64/roms/` (Not64), **PlayStation** → `/wiisx/isos/` (WiiSX).
- **Borrar** un juego deja sitio en el disco; las partidas no se tocan.
- Descargas en cola, en segundo plano y **reanudables**: si se corta la red o se
  sale de la app, la próxima vez que se instala sigue donde lo dejó.
- Carátulas, idiomas, aviso de archivo corrupto y «Solicitar acceso», como en
  la app de Android.
- **Se actualiza sola** desde sund.es (al arrancar y en el menú, «Buscar
  actualizaciones»), y avisa de las **versiones nuevas de los juegos
  instalados** (otro SHA en la librería): se actualizan de uno en uno o todos
  a la vez, y la versión anterior solo se quita cuando la nueva está entera.
- Se puede abrir **como canal** del menú de Wii (ver abajo).

C, devkitPPC, libogc, SDL2 y mbedTLS (HTTPS); fuentes y certificados raíz
van dentro de `boot.dol`.

## El disco USB

RomsLibrary escribe en el disco con libfat, así que el disco tiene que ser:

- **FAT32**. NTFS, exFAT y las particiones WBFS no valen (USB Loader GX sí
  lee NTFS, RomsLibrary no). La app dice al arrancar qué le pasa al disco si
  no lo puede usar.
- Con **tabla de particiones MBR** (no GPT) y la partición FAT32 como
  **primera partición primaria**. Con MBR, como mucho **2 TB**.
- Tamaño de clúster recomendado: **32 KB** (lo que recomienda USB Loader GX).
- En FAT32 ningún fichero puede llegar a 4 GB: los `.wbfs` grandes van
  partidos (`.wbfs` + `.wbf1`...), y RomsLibrary los parte solo al instalar.
  Un `.iso` de Wii de más de 4 GB no cabe: conviértelo a `.wbfs` (Wii Backup
  Manager en Windows, `wit copy --wbfs` en Linux).

Cómo comprobarlo en Windows: botón derecho en el disco → Propiedades →
«Sistema de archivos». Para formatearlo (**se borra todo**; copia antes los
juegos): Windows no ofrece FAT32 en discos de más de 32 GB, así que usa Rufus
(«Large FAT32») o «FAT32 Format» (guiformat). En Linux:

    sudo parted /dev/sdX --script mklabel msdos mkpart primary fat32 1MiB 100%
    sudo mkfs.vfat -F 32 -s 64 -n WII /dev/sdX1      # 32 KB por clúster (sectores de 512 B)

Los discos de 2,5" sin alimentación propia pueden necesitar un cable en Y.

## Canal en el menú de Wii

`RomsLibrary-canal.wad` (en las Releases `wii-v…` y en sund.es:
`accion=wii&fichero=RomsLibrary-canal.wad`)
pone RomsLibrary como canal. El canal solo lleva un cargador que abre
`apps/romslibrary/boot.dol` de la SD (o del USB): la app tiene que seguir ahí, y
como se actualiza sola en esa carpeta, el canal no hay que reinstalarlo.

Se instala desde la propia app (**1 → «Instalar como canal»**: descarga el WAD
de sund.es, comprueba su SHA-256, recarga el cIOS 249 de d2x e instala con ES;
después la app se cierra; ajuste `ios_canal` para otro IOS) o con un gestor de
WAD que use el cIOS (p. ej. **YAWM ModMii Edition**). La app avisa si hay una
versión nueva del canal. Antes, ten una copia de la NAND con BootMii
y Priiloader instalado: es la red de seguridad de cualquier canal homebrew.
Se desinstala con el mismo gestor (título 00010001-RMLB).

Lo construye `canal/construir_canal.sh` (con Docker): el cargador (`canal/cargador`,
enlazado en 0x81330000 como el del Homebrew Channel), el banner y el icono
(`canal/banner`, con Alameda y las herramientas del Homebrew Channel, GPLv2,
descargadas en una versión fija) y el WAD con firma falsa (pywii). Necesita la
clave común de la Wii en `~/.secrets/wii/common-key` de quien lo construye, que
no va en el repositorio.

## Instalar

1. Descarga `RomsLibrary-Wii.zip` de las [Releases](https://github.com/IvanFreireDacoba/RomsLibrary/releases) (las `wii-v…`)
   o de `https://sund.es/APIs/RomLibraryApp?accion=wii&fichero=RomsLibrary-Wii.zip`.
2. Descomprímelo en la raíz de la SD (o del USB): queda `apps/romslibrary/`.
3. Ábrela desde el Homebrew Channel.

La primera vez: **1 → Solicitar acceso** y acéptalo en el gestor de la
librería (Accesos). Sin acceso solo se ven los juegos que la librería comparte.

## Controles

| | Wiimote | Mando Clásico | GameCube |
|---|---|---|---|
| Moverse / página | Cruceta | Cruceta | Cruceta o palanca |
| Instalar · opciones (actualizar, borrar) · cancelar | A | A | A |
| Volver | B | B | B |
| Consola anterior / siguiente | − / + | L / R (o − / +) | L / R |
| Menú (acceso, actualizaciones, ajustes) | 1 | X | Start |
| Idioma | 2 | Y | Y |
| Salir | HOME | HOME | Z |

## Ajustes

`apps/romslibrary/ajustes.txt` (se puede editar en un PC): `url`, `clave`,
`juegos=usb|sd` (Wii y GameCube), `roms=usb|sd` (emuladores) y
`carpeta_<consola>=/ruta` para cambiar la carpeta de un emulador (p. ej.
`carpeta_gba=/retroarch/roms/gba`). Lo instalado se apunta en `instalados.txt`.

## Desarrollo

    sudo bash empaquetado/construir.sh          # con Docker: dist/apps/romslibrary + RomsLibrary-Wii.zip
    sudo bash empaquetado/construir.sh linux    # versión de PC para probar (plataforma_linux.c)

La versión está en `fuente/version.h`. La versión de PC usa carpetas como
unidades (`RL_APP`, `RL_USB`, `RL_SD`) y admite pruebas automáticas con
`RL_TECLAS` (ver `fuente/guion.h`); en la Wii, con `guion-pruebas.txt` junto a
`boot.dol`. Dolphin (con `--shm-size=2g` en Docker) carga `boot.dol` y tiene
red y SD, pero no USB.

Normas de uso y aviso legal: [LEGAL.md](../LEGAL.md).
