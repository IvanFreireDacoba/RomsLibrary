"""
Partidas (RetroArch) sincronizadas con el servidor, en el MISMO formato que la
app de Android: así una partida empezada en una Retroid sigue en una consola
con Knulli o en la Steam Deck, y al revés.

Cada partida es una entrada sistema / clave (nombre del ROM sin extensión) /
nombre "<saves|states>__<núcleo>__<fichero>". En cada sincronización gana la
copia más reciente; al bajar se restaura la fecha original (si no, parecería
más nueva y se volvería a subir en bucle).

Dónde guarda RetroArch:
- "retroarch": se lee retroarch.cfg (savefile_directory, savestate_directory,
  si separa por núcleo, o si guarda junto al ROM).
- "por_sistema" (Knulli/Batocera): <partidas>/<consola>/<rom>.srm|.state*,
  sin carpeta por núcleo: se suben con el núcleo habitual de esa consola
  (plataforma.NUCLEO_POR_DEFECTO) y solo se bajan las de ese núcleo.

Fuera de RetroArch (PCSX2, Dolphin, Ryujinx...) no se sincroniza todavía.
Nunca impide instalar ni desinstalar: los fallos van al registro.
"""

import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import api, catalogo, partidas, plataforma
from .ajustes import carpeta_datos

TOLERANCIA = 2.0
REGISTRO = carpeta_datos() / "guardados.log"


def log(texto: str) -> None:
    try:
        with open(REGISTRO, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + texto + "\n")
    except OSError:
        pass


@dataclass
class Entrada:
    sistema: str
    clave: str
    nombre: str
    local: Path

    @property
    def id(self) -> str:
        return f"{self.sistema}/{self.clave}/{self.nombre}"


@dataclass
class Disposicion:
    """Dónde deja RetroArch las partidas en esta máquina."""
    saves: Path | None              # None = junto al ROM
    states: Path | None
    por_nucleo_saves: bool
    por_nucleo_states: bool


def _leer_cfg(ruta: str) -> dict:
    valores = {}
    try:
        for linea in Path(ruta).read_text(errors="replace").splitlines():
            if "=" in linea and not linea.lstrip().startswith("#"):
                k, v = linea.split("=", 1)
                valores[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return valores


def _ruta_ra(valor: str, cfg: str) -> Path | None:
    if not valor or valor == "default":
        return None
    if valor.startswith(":"):                       # relativa a la carpeta de RetroArch
        return Path(cfg).parent / valor.lstrip(":\\/")
    return Path(os.path.expanduser(valor))


def disposicion(ajustes) -> Disposicion | None:
    p = ajustes.plataforma
    if p.partidas == "por_sistema":
        base = Path(ajustes["partidas"] or p.carpeta_partidas)
        return Disposicion(base, base, False, False)
    for cfg in p.retroarch_cfg:
        if os.path.isfile(cfg):
            v = _leer_cfg(cfg)
            si = lambda k: v.get(k, "false") == "true"  # noqa: E731
            saves = None if si("savefiles_in_content_dir") else _ruta_ra(v.get("savefile_directory", ""), cfg)
            states = None if si("savestates_in_content_dir") else _ruta_ra(v.get("savestate_directory", ""), cfg)
            return Disposicion(saves, states, si("sort_savefiles_enable"), si("sort_savestates_enable"))
    if p.id == "muos":
        return Disposicion(Path("/mnt/mmc/MUOS/save/file"), Path("/mnt/mmc/MUOS/save/state"), True, True)
    return None


def _carpeta_tipo(d: Disposicion, tipo: str, ajustes, sistema: str, rom: Path) -> tuple[Path | None, bool]:
    base, por_nucleo = (d.saves, d.por_nucleo_saves) if tipo == "saves" else (d.states, d.por_nucleo_states)
    if ajustes.plataforma.partidas == "por_sistema":
        return base / ajustes.carpeta_sistema(sistema).name, False
    if base is None:
        return (rom if rom.is_dir() else rom.parent), False
    return base, por_nucleo


def entradas_de(ajustes, sistema: str, fichero: str, remotos: dict) -> list[Entrada]:
    d = disposicion(ajustes)
    if d is None or sistema not in plataforma.NUCLEO_POR_DEFECTO:
        return []
    stem = Path(fichero).stem if Path(fichero).suffix else fichero
    rom = ajustes.carpeta_sistema(sistema) / fichero
    if not rom.exists() and (rom.parent / stem).is_dir():
        rom = rom.parent / stem                    # grupo instalado como carpeta "Juego/" (sin ES-DE)
    nucleo_def = plataforma.NUCLEO_POR_DEFECTO[sistema]
    lista: dict[str, Entrada] = {}
    for tipo in ("saves", "states"):
        carpeta, por_nucleo = _carpeta_tipo(d, tipo, ajustes, sistema, rom)
        if carpeta is None:
            continue
        nucleos = ([c for c in carpeta.iterdir() if c.is_dir()] if carpeta.is_dir() else []) if por_nucleo else [None]
        for c in nucleos:
            dentro = c if c is not None else carpeta
            try:
                ficheros = [f for f in dentro.iterdir() if f.is_file() and f.name.startswith(stem + ".")]
            except OSError:
                continue
            for f in ficheros:
                if tipo == "saves" and f.suffix.lower() not in (".srm", ".sav", ".mcd", ".mpk", ".eep", ".fla", ".sra"):
                    continue
                if tipo == "states" and ".state" not in f.name:
                    continue
                e = Entrada(sistema, stem, f"{tipo}__{c.name if c else nucleo_def}__{f.name}", f)
                lista[e.id] = e
        # Las que solo están en el servidor (para bajarlas).
        prefijo = f"{sistema}/{stem}/{tipo}__"
        for id_ in remotos:
            if not id_.startswith(prefijo):
                continue
            partes = id_[len(f"{sistema}/{stem}/"):].split("__", 2)
            if len(partes) != 3 or not all(catalogo.componente_seguro(x) for x in partes[1:]):
                continue
            if por_nucleo:
                local = carpeta / partes[1] / partes[2]
            elif partes[1] == nucleo_def:
                local = carpeta / partes[2]
            else:
                continue
            e = Entrada(sistema, stem, id_[len(f"{sistema}/{stem}/"):], local)
            lista.setdefault(e.id, e)
    return list(lista.values())


# ---------------- servicio de partidas ----------------

def _indice(ajustes) -> dict[str, float]:
    return {f"{f['sistema']}/{f['clave']}/{f['nombre_fichero']}": float(f["mtime"]) for f in partidas.indice(ajustes)}


def _sincronizar_entrada(ajustes, e: Entrada, remoto: float | None) -> int:
    local = e.local.stat().st_mtime if e.local.is_file() else 0
    remoto = remoto or 0
    if local and local > remoto + TOLERANCIA:
        partidas.subir(ajustes, {"sistema": e.sistema, "clave": e.clave, "nombre_fichero": e.nombre,
                                 "mtime_dispositivo": str(local)}, e.nombre, str(e.local))
        log(f"subido {e.id}")
        return 1
    if remoto and remoto > local + TOLERANCIA:
        e.local.parent.mkdir(parents=True, exist_ok=True)
        parcial = e.local.with_name(e.local.name + ".bajando")
        parcial.unlink(missing_ok=True)
        partidas.bajar(ajustes, e.sistema, e.clave, e.nombre, str(parcial))
        os.replace(parcial, e.local)
        os.utime(e.local, (remoto, remoto))
        log(f"bajado {e.id}")
        return -1
    return 0


def sincronizar(ajustes, juegos: list[tuple[str, str]]) -> tuple[int, int]:
    """juegos: [(sistema, nombre en la librería)]. Devuelve (subidos, bajados)."""
    if not ajustes.identificador:
        return 0, 0
    subidos = bajados = 0
    try:
        remotos = _indice(ajustes)
    except api.ErrorApi as e:
        log(f"ERROR leyendo el índice de partidas: {e}")
        return 0, 0
    vistas = set()
    for sistema, fichero in juegos:
        for e in entradas_de(ajustes, sistema, fichero, remotos):
            if e.id in vistas:
                continue
            vistas.add(e.id)
            try:
                r = _sincronizar_entrada(ajustes, e, remotos.get(e.id))
                subidos += r > 0
                bajados += r < 0
            except (api.ErrorApi, OSError) as ex:
                log(f"ERROR sincronizando {e.id}: {ex}")
    return subidos, bajados


def sincronizar_juego(ajustes, sistema: str, fichero: str) -> None:
    sincronizar(ajustes, [(sistema, fichero)])
