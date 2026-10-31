"""
Código de juego leído de la CABECERA del propio ROM (nunca del nombre del
fichero, que lo pone quien sube). Sirve para validar las actualizaciones: una
versión nueva solo se acepta si su código coincide con el del juego que
sustituye. Así no se puede «actualizar» un juego con otro distinto.

    codigo(ruta) -> ("gba:ABCD", "TITULO INTERNO") | None

None = formato sin cabecera legible (comprimidos: .chd, .cso, .rvz...): el
gestor pide entonces confirmación explícita.

Lo que se lee, por sistema:
- GB/GBC: título interno (0x134).           - GBA: código de juego (0xAC).
- N64: código de juego (0x3B), con cualquier orden de bytes (z64/v64/n64).
- GameCube (.iso/.gcm): ID de disco (0x00).
- Wii (.iso/.wbfs): ID de disco (0x00; en .wbfs, en la copia de la cabecera
  del disco que va en el sector 1, 0x200).
- PS1/PS2: número de serie del arranque (SYSTEM.CNF dentro del disco).
- PSP: número de serie (UMD_DATA.BIN dentro del disco).
- .zip: la cabecera del ROM que va dentro.
"""

import re
import struct
import zipfile
from pathlib import Path


def _txt(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("ascii", "replace").strip()


def _imprimible(t: str) -> bool:
    return bool(t) and all(32 <= ord(c) < 127 for c in t)


# ---------------- cartuchos ----------------

def _gb(cab: bytes):
    if len(cab) < 0x150 or cab[0x104:0x108] != b"\xce\xed\x66\x66":     # logo de Nintendo
        return None
    # En los de Game Boy Color el último byte (0x143) es la marca de color, no del título.
    titulo = _txt(cab[0x134:0x143] if cab[0x143] & 0x80 else cab[0x134:0x144])
    return ("gb:" + titulo, titulo) if _imprimible(titulo) else None


def _gba(cab: bytes):
    if len(cab) < 0xC0 or cab[0xB2] != 0x96:                           # byte fijo de la cabecera
        return None
    codigo, titulo = _txt(cab[0xAC:0xB0]), _txt(cab[0xA0:0xAC])
    return ("gba:" + codigo, titulo) if _imprimible(codigo) else None


def _nds(cab: bytes):
    # Cabecera de cartucho NDS: título en 0x00 (12 bytes), código de juego en
    # 0x0C (4), y el CRC del logo de Nintendo (idéntico en todos los cartuchos
    # oficiales) en 0x15C = 0xCF56 -> bytes 56 CF. Sirve de marca de validez,
    # igual que el byte fijo 0x96 del GBA o el logo del GB.
    if len(cab) < 0x15E or cab[0x15C:0x15E] != b"\x56\xCF":
        return None
    codigo, titulo = _txt(cab[0x0C:0x10]), _txt(cab[0x00:0x0C])
    return ("nds:" + codigo, titulo) if _imprimible(codigo) else None


def _n64(cab: bytes):
    if len(cab) < 0x40:
        return None
    marca = cab[:4]
    if marca == b"\x80\x37\x12\x40":                                     # .z64 (big endian)
        h = cab[:0x40]
    elif marca == b"\x37\x80\x40\x12":                                   # .v64 (bytes cambiados de 2 en 2)
        h = bytes(b for i in range(0, 0x40, 2) for b in (cab[i + 1], cab[i]))
    elif marca == b"\x40\x12\x37\x80":                                   # .n64 (little endian)
        h = b"".join(cab[i:i + 4][::-1] for i in range(0, 0x40, 4))
    else:
        return None
    codigo, titulo = _txt(h[0x3B:0x3F]), _txt(h[0x20:0x34])
    return ("n64:" + codigo, titulo) if _imprimible(codigo) else None


def _gc(cab: bytes):
    if len(cab) < 0x40 or cab[0x1C:0x20] != b"\xc2\x33\x9f\x3d":         # marca mágica de GameCube
        return None
    codigo, titulo = _txt(cab[0:6]), _txt(cab[0x20:0x60])
    return ("gc:" + codigo, titulo) if _imprimible(codigo) else None


def _wii(cab: bytes):
    if cab[:4] == b"WBFS":                                               # .wbfs: la cabecera del disco va en 0x200
        cab = cab[0x200:]
    if len(cab) < 0x60 or cab[0x18:0x1C] != b"\x5d\x1c\x9e\xa3":         # marca mágica de Wii
        return None
    codigo, titulo = _txt(cab[0:6]), _txt(cab[0x20:0x60])
    return ("wii:" + codigo, titulo) if _imprimible(codigo) else None


# ---------------- discos (ISO 9660) ----------------

class _Disco:
    """Lee sectores de 2048 bytes de una imagen .iso o .bin (MODE1/MODE2 2352)."""

    def __init__(self, f):
        self.f = f
        for tam, desp in ((2048, 0), (2352, 24), (2352, 16)):
            f.seek(16 * tam + desp)
            if f.read(6)[1:6] == b"CD001":
                self.tam, self.desp = tam, desp
                return
        raise ValueError("no es un disco ISO 9660")

    def sector(self, n: int, cuantos: int = 1) -> bytes:
        datos = b""
        for i in range(cuantos):
            self.f.seek((n + i) * self.tam + self.desp)
            datos += self.f.read(2048)
        return datos

    def fichero_raiz(self, nombre: str, maximo: int = 65536) -> bytes | None:
        pvd = self.sector(16)
        raiz = pvd[156:190]
        lba, tam = struct.unpack_from("<I", raiz, 2)[0], struct.unpack_from("<I", raiz, 10)[0]
        directorio = self.sector(lba, min((tam + 2047) // 2048, 8))
        i = 0
        while i < len(directorio):
            largo = directorio[i]
            if largo == 0:                                               # relleno hasta el siguiente sector
                i = (i // 2048 + 1) * 2048
                continue
            reg = directorio[i:i + largo]
            n = reg[32]
            nom = reg[33:33 + n].decode("ascii", "replace").split(";")[0].upper()
            if nom == nombre.upper():
                lba_f, tam_f = struct.unpack_from("<I", reg, 2)[0], struct.unpack_from("<I", reg, 10)[0]
                return self.sector(lba_f, (min(tam_f, maximo) + 2047) // 2048)[:min(tam_f, maximo)]
            i += largo
        return None


_SERIE = re.compile(r"([A-Z]{4})[_-]?(\d{3})\.?(\d{2})")


def _psx_ps2(f, sistema: str):
    try:
        cnf = _Disco(f).fichero_raiz("SYSTEM.CNF", 4096)
    except (ValueError, struct.error, IndexError):
        return None
    if not cnf:
        return None
    m = _SERIE.search(cnf.decode("ascii", "replace").upper())
    return (f"{sistema}:{m.group(1)}-{m.group(2)}{m.group(3)}", f"{m.group(1)}-{m.group(2)}{m.group(3)}") if m else None


def _psp(f):
    try:
        umd = _Disco(f).fichero_raiz("UMD_DATA.BIN", 256)
    except (ValueError, struct.error, IndexError):
        return None
    if not umd:
        return None
    serie = umd.split(b"|", 1)[0].decode("ascii", "replace").strip()
    return ("psp:" + serie, serie) if re.fullmatch(r"[A-Z]{4}-\d{5}", serie) else None


# ---------------- entrada ----------------

_CARTUCHOS = {".gb": _gb, ".gbc": _gb, ".gba": _gba, ".nds": _nds, ".n64": _n64, ".z64": _n64, ".v64": _n64}


def _de_flujo(f, extension: str, sistema: str):
    if extension in _CARTUCHOS:
        return _CARTUCHOS[extension](f.read(0x200))
    if sistema == "gc" and extension in (".iso", ".gcm"):
        return _gc(f.read(0x60))
    if sistema == "wii" and extension in (".iso", ".wbfs"):
        return _wii(f.read(0x260))
    if sistema in ("psx", "ps2") and extension in (".iso", ".bin"):
        return _psx_ps2(f, sistema)
    if sistema == "psp" and extension == ".iso":
        return _psp(f)
    return None


def roms_en_zip(ruta: Path) -> list[str]:
    """Nombres de los miembros con formato de ROM de cartucho dentro de un .zip,
    ignorando la basura típica de un fan game zipeado (carpetas, readmes, y los
    restos de macOS: __MACOSX/, ._* y .DS_Store). Lo usan `codigo` (para leer la
    ÚNICA ROM que lleve dentro) y la limpieza de subidas (ver
    versiones.limpiar_zip_cartucho)."""
    try:
        with zipfile.ZipFile(ruta) as z:
            return [i.filename for i in z.infolist()
                    if not i.is_dir()
                    and not i.filename.startswith("__MACOSX/")
                    and not Path(i.filename).name.startswith("._")
                    and Path(i.filename).suffix.lower() in _CARTUCHOS]
    except (OSError, zipfile.BadZipFile):
        return []


def codigo(ruta: Path, sistema: str, nombre: str | None = None):
    """Código del juego (y título interno) del ROM en `ruta`. `nombre` da la
    extensión si `ruta` es un temporal sin ella (subida en curso)."""
    extension = Path(nombre or ruta.name).suffix.lower()
    try:
        if extension == ".zip":
            roms = roms_en_zip(ruta)
            if len(roms) != 1:
                return None                                            # ninguna o varias ROMs: sin código claro
            with zipfile.ZipFile(ruta) as z, z.open(roms[0]) as f:
                return _CARTUCHOS[Path(roms[0]).suffix.lower()](f.read(0x200))
        with open(ruta, "rb") as f:
            return _de_flujo(f, extension, sistema)
    except (OSError, zipfile.BadZipFile, ValueError):
        return None


# ---------------- validación por CONTENIDO (normalizar subidas) ----------------

# sistema -> detector de cartucho crudo. Para reconocer el juego por su
# contenido aunque llegue en crudo (p. ej. un .gba suelto) cuando se guarda
# comprimido en .zip.
_CARTUCHO_POR_SISTEMA = {"gb": _gb, "gbc": _gb, "gba": _gba,
                         "n64": _n64, "z64": _n64, "v64": _n64}


def es_zip(ruta: Path) -> bool:
    try:
        return zipfile.is_zipfile(ruta)
    except OSError:
        return False


def codigo_crudo(ruta: Path, sistema: str):
    """Como `codigo`, pero leyendo el fichero como CARTUCHO crudo del sistema,
    sin mirar su nombre/extensión. Devuelve None si el sistema no es de
    cartucho o si el contenido no es un cartucho válido de ese sistema."""
    det = _CARTUCHO_POR_SISTEMA.get(sistema)
    if det is None:
        return None
    try:
        with open(ruta, "rb") as f:
            return det(f.read(0x200))
    except OSError:
        return None
