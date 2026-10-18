"""
Dónde está cada cosa en cada sistema. La librería usa sus propios nombres de
consola (los de ES-DE: gb, gbc, gba, n64, gc, switch, psx, ps2, psp,
rpgmaker); aquí se traducen a la carpeta que espera cada frontend y se
proponen las rutas de ROMs y de partidas. Todo se puede cambiar en Ajustes
(la carpeta de cada consola también: en muOS cada uno nombra las suyas).

Partidas: solo las de RetroArch (lo que usan casi todas las consolas Linux y
EmuDeck/RetroArch en la Deck y la Ally). Ver guardados.py.
"""

import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

SISTEMAS = ["gb", "gbc", "gba", "n64", "gc", "switch", "psx", "ps2", "psp", "rpgmaker"]
NOMBRES = {"gb": "Game Boy", "gbc": "Game Boy Color", "gba": "Game Boy Advance", "n64": "Nintendo 64",
           "gc": "GameCube", "switch": "Switch", "psx": "PlayStation", "ps2": "PlayStation 2", "psp": "PSP",
           "rpgmaker": "RPG Maker"}

# Batocera/Knulli y ROCKNIX llaman "gamecube" a la carpeta de GameCube.
_CARPETAS_BATOCERA = {"gc": "gamecube"}
# muOS: nombres que reconoce su asignación automática de núcleos; si el
# usuario ya tiene otra carpeta para esa consola, se usa la suya (ver
# carpeta_existente).
_ALIAS_MUOS = {
    "gb": ["gb", "Nintendo Game Boy", "Game Boy"],
    "gbc": ["gbc", "Nintendo Game Boy Color", "Game Boy Color"],
    "gba": ["gba", "Nintendo Game Boy Advance", "Game Boy Advance"],
    "n64": ["n64", "Nintendo N64", "Nintendo 64"],
    "psx": ["psx", "ps", "ps1", "Sony PlayStation", "PlayStation"],
    "psp": ["psp", "Sony PlayStation Portable", "PSP"],
    "gc": ["gc", "gamecube", "Nintendo GameCube"],
    "ps2": ["ps2", "Sony PlayStation 2"],
    "switch": ["switch", "Nintendo Switch"],
}

# Núcleo de RetroArch con el que se nombran en el servidor las partidas de
# frontends que NO separan por núcleo (Knulli: /userdata/saves/<consola>/).
# Son los mismos que usan las Retroid, así las partidas pasan de una a otra.
NUCLEO_POR_DEFECTO = {"gb": "Gambatte", "gbc": "Gambatte", "gba": "mGBA", "n64": "Mupen64Plus-Next",
                      "psx": "PCSX-ReARMed", "psp": "PPSSPP"}


@dataclass
class Plataforma:
    id: str
    nombre: str
    roms: str                                  # carpeta ROMs propuesta
    sistemas: list[str]                        # consolas que este aparato puede mover
    carpetas: dict[str, str] = field(default_factory=dict)   # sistema -> nombre de carpeta (si no es el mismo)
    # Partidas de RetroArch: "retroarch" (lee retroarch.cfg), "por_sistema"
    # (<partidas>/<carpeta de consola>/, Knulli) o "ninguna".
    partidas: str = "retroarch"
    carpeta_partidas: str = ""
    retroarch_cfg: list[str] = field(default_factory=list)
    alias: dict[str, list[str]] = field(default_factory=dict)
    grupos_como_carpeta: bool = False          # ES-DE: "Juego.cue/" cuenta como un juego


def _arquitectura() -> str:
    m = platform.machine().lower()
    return "aarch64" if m in ("aarch64", "arm64", "armv8l") else ("x86_64" if m in ("x86_64", "amd64") else m)


def _leer(ruta: str) -> str:
    try:
        return Path(ruta).read_text(errors="replace")
    except OSError:
        return ""


def detectar() -> Plataforma:
    casa = Path.home()
    ligeras = ["gb", "gbc", "gba", "n64", "psx", "psp"]          # lo que mueve cualquier portátil ARM
    pc = [s for s in SISTEMAS if s != "rpgmaker"]
    if sys.platform == "win32":
        return Plataforma("windows", "Windows", str(casa / "ROMs"), SISTEMAS[:],
                          retroarch_cfg=[os.path.join(os.environ.get("APPDATA", ""), "RetroArch", "retroarch.cfg"),
                                         r"C:\RetroArch-Win64\retroarch.cfg",
                                         os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                                                      "RetroArch", "retroarch.cfg")],
                          grupos_como_carpeta=True)
    if os.path.isdir("/userdata/roms") or "batocera" in _leer("/etc/os-release").lower() \
            or "knulli" in _leer("/etc/os-release").lower():
        nombre = "Knulli" if "knulli" in (_leer("/etc/os-release") + _leer("/usr/share/batocera/batocera.version")).lower() \
            else "Batocera"
        return Plataforma("batocera", nombre, "/userdata/roms", ligeras if _arquitectura() == "aarch64" else pc,
                          carpetas=dict(_CARPETAS_BATOCERA), partidas="por_sistema", carpeta_partidas="/userdata/saves")
    if os.path.isdir("/opt/muos") or os.path.isdir("/mnt/mmc/MUOS"):
        roms = "/mnt/sdcard/ROMS" if os.path.isdir("/mnt/sdcard/ROMS") else "/mnt/mmc/ROMS"
        return Plataforma("muos", "muOS", roms, ligeras,
                          retroarch_cfg=["/mnt/mmc/MUOS/retroarch/retroarch.cfg", "/run/muos/storage/info/config/retroarch.cfg"],
                          alias=_ALIAS_MUOS)
    if os.path.isdir("/storage/roms"):
        nombre = "ROCKNIX" if "rocknix" in _leer("/etc/os-release").lower() else "JELOS/ROCKNIX"
        return Plataforma("rocknix", nombre, "/storage/roms", ligeras if _arquitectura() == "aarch64" else pc,
                          carpetas=dict(_CARPETAS_BATOCERA),
                          retroarch_cfg=["/storage/.config/retroarch/retroarch.cfg"])
    flatpak = casa / ".var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg"
    cfgs = [str(flatpak), str(casa / ".config/retroarch/retroarch.cfg"),
            str(casa / ".local/share/Steam/steamapps/common/RetroArch/retroarch.cfg")]
    if (casa / "Emulation/roms").is_dir():
        return Plataforma("emudeck", "EmuDeck", str(casa / "Emulation/roms"), pc, retroarch_cfg=cfgs,
                          grupos_como_carpeta=True)
    esde = casa / "ES-DE/settings/es_settings.xml"
    if esde.is_file():
        texto = _leer(str(esde))
        roms = str(casa / "ROMs")
        marca = '<string name="ROMDirectory" value="'
        if marca in texto:
            roms = texto.split(marca, 1)[1].split('"', 1)[0].replace("~", str(casa)) or roms
        return Plataforma("esde", "ES-DE", roms, pc, retroarch_cfg=cfgs, grupos_como_carpeta=True)
    nombre = "Steam Deck" if "valve" in _leer("/sys/devices/virtual/dmi/id/board_vendor").lower() else "Linux"
    return Plataforma("linux", nombre, str(casa / "ROMs"), pc if _arquitectura() == "x86_64" else ligeras,
                      retroarch_cfg=cfgs)


def carpeta_existente(raiz: Path, alias: list[str]) -> str | None:
    """Primera carpeta de la raíz que coincide (sin mayúsculas) con un alias."""
    try:
        existentes = {p.name.lower(): p.name for p in raiz.iterdir() if p.is_dir()}
    except OSError:
        return None
    for a in alias:
        if a.lower() in existentes:
            return existentes[a.lower()]
    return None


def arquitectura() -> str:
    return _arquitectura()


def id_paquete() -> str:
    """Nombre del paquete de la app para esta máquina (ver autoactualizar.py)."""
    if sys.platform == "win32":
        return "windows-x86_64"
    return f"linux-{_arquitectura()}"
