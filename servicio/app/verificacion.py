"""
Verificación de los juegos contra las bases de volcados buenos: No-Intro
(cartuchos) y Redump (discos), las que usa RetroArch. Se descargan de
libretro-database (CC-BY-SA 4.0) y se guardan en /datos/dats (se renuevan cada
DIAS_DATS); no van incluidas en el código.

Para cada juego (su versión actual, por SHA-256) se calcula el SHA-1 del
contenido como lo calculan esas bases: el ROM de dentro si va en .zip, los de
N64 en orden de bytes .z64, y en los juegos de varios ficheros (PS1 cue+bin)
cada pista. Resultado:

- verificado:  coincide con un volcado oficial (se guarda su nombre).
- dudoso:      SOLO discos: hay un volcado oficial con el mismo número de serie
               (el de la cabecera) y EL MISMO TAMAÑO, pero otro contenido: datos
               alterados (disco dañado o imagen modificada). Suele no arrancar.
- otro_volcado: mismo número de serie pero otro tamaño: otro programa de volcado
               u otro corte de pistas (frecuente en PS1). Normalmente funciona.
- desconocido: no está en la base. Normal en romhacks, traducciones y homebrew.
- sin_base:    sistema o formato que no se puede comprobar (Switch, RPG Maker,
               .chd, .cso, .rvz...).

Es un aviso para el administrador: nunca impide subir ni actualizar nada.
"""

import array
import hashlib
import re
import sqlite3
import threading
import time
import zipfile
from pathlib import Path

import httpx

import cabeceras
import config

BASE = "https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/"
DATS = {
    "gb": ("No-Intro", "no-intro/Nintendo - Game Boy.dat"),
    "gbc": ("No-Intro", "no-intro/Nintendo - Game Boy Color.dat"),
    "gba": ("No-Intro", "no-intro/Nintendo - Game Boy Advance.dat"),
    "nds": ("No-Intro", "no-intro/Nintendo - Nintendo DS.dat"),
    "n64": ("No-Intro", "no-intro/Nintendo - Nintendo 64.dat"),
    "gc": ("Redump", "redump/Nintendo - GameCube.dat"),
    "wii": ("Redump", "redump/Nintendo - Wii.dat"),
    "psx": ("Redump", "redump/Sony - PlayStation.dat"),
    "ps2": ("Redump", "redump/Sony - PlayStation 2.dat"),
    "psp": ("Redump", "redump/Sony - PlayStation Portable.dat"),
}
DISCOS = {"gc", "wii", "psx", "ps2", "psp"}
NINTENDO_DISCO = {"gc", "wii"}
# Extensiones cuyo contenido es el que listan las bases (no comprimidos ni índices).
VERIFICABLES = {".gb", ".gbc", ".gba", ".nds", ".n64", ".z64", ".v64", ".iso", ".gcm", ".bin", ".zip"}
DIAS_DATS = 14
CARPETA = config.DATOS / "dats"


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def inicializar() -> None:
    CARPETA.mkdir(parents=True, exist_ok=True)
    with _conectar() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS verificaciones (
            sha TEXT PRIMARY KEY, estado TEXT NOT NULL, fuente TEXT NOT NULL DEFAULT '',
            nombre TEXT NOT NULL DEFAULT '', detalle TEXT NOT NULL DEFAULT '', fecha REAL NOT NULL)""")


# ---------------- bases (DAT) ----------------

_bases: dict[str, dict] = {}      # sistema -> {"sha1": {...}, "serie": {...}, "mtime": float}
_ROM = re.compile(r'rom \( name "(?P<nombre>[^"]*)" size (?P<tam>\d+)(?: crc \w+)?(?: md5 \w+)? sha1 (?P<sha1>[0-9A-Fa-f]{40})')
_SERIE = re.compile(r'^\s*serial "([^"]+)"', re.M)
_NOMBRE = re.compile(r'^\s*name "([^"]+)"', re.M)


def _ruta_dat(sistema: str) -> Path:
    return CARPETA / Path(DATS[sistema][1]).name


def _descargar_dat(sistema: str) -> None:
    ruta = _ruta_dat(sistema)
    if ruta.is_file() and time.time() - ruta.stat().st_mtime < DIAS_DATS * 86400:
        return
    try:
        r = httpx.get(BASE + DATS[sistema][1], timeout=60, follow_redirects=True)
        if r.status_code == 200 and b"game (" in r.content:
            temporal = ruta.with_suffix(".tmp")
            temporal.write_bytes(r.content)
            temporal.replace(ruta)
    except httpx.HTTPError as e:
        print(f"[verificacion] no se pudo descargar la base de {sistema}: {e}", flush=True)


def _serie_base(serie: str) -> str:
    """"SCES-52456-ANZ/P" -> "SCES-52456" (el de la cabecera no lleva sufijos).
    GameCube/Wii: "DL-DOL-GALE-USA" / "RVL-RMGE-USA-B0" -> "GALE" / "RMGE", los
    4 primeros caracteres del ID de la cabecera (los 2 últimos son la editora)."""
    m = re.match(r"(?:DL-DOL|RVL)-([A-Z0-9]{4})(?:-|$)", serie.upper())
    if m:
        return m.group(1)
    m = re.match(r"([A-Z]{4})[-_]?(\d{3})\.?(\d{2})", serie.upper())
    return f"{m.group(1)}-{m.group(2)}{m.group(3)}" if m else serie.upper()


def base(sistema: str) -> dict | None:
    ruta = _ruta_dat(sistema)
    if not ruta.is_file():
        return None
    guardada = _bases.get(sistema)
    if guardada and guardada["mtime"] == ruta.stat().st_mtime:
        return guardada
    por_sha1, por_serie = {}, {}     # por_serie: serie -> {nombre del juego: tamaño total de sus datos}
    for bloque in ruta.read_text(encoding="utf-8", errors="replace").split("\ngame (")[1:]:
        nombre = _NOMBRE.search(bloque)
        series = [_serie_base(s) for s in _SERIE.findall(bloque)]
        for m in _ROM.finditer(bloque):
            por_sha1[m.group("sha1").lower()] = {"juego": nombre.group(1) if nombre else m.group("nombre"),
                                                "fichero": m.group("nombre"), "tamano": int(m.group("tam"))}
        total = sum(int(m.group("tam")) for m in _ROM.finditer(bloque)
                    if Path(m.group("nombre")).suffix.lower() in (".bin", ".iso", ".gcm"))
        for s in series:
            por_serie.setdefault(s, {})[nombre.group(1) if nombre else ""] = total
    _bases[sistema] = {"sha1": por_sha1, "serie": por_serie, "mtime": ruta.stat().st_mtime}
    return _bases[sistema]


# ---------------- huellas ----------------

def _sha1_flujo(f, n64: str | None = None) -> str:
    h = hashlib.sha1()
    while trozo := f.read(4 * 1024 * 1024):
        if n64 == "v64":                                        # bytes cambiados de 2 en 2
            b = bytearray(trozo)
            b[0::2], b[1::2] = trozo[1::2], trozo[0::2]
            trozo = bytes(b)
        elif n64 == "n64" and len(trozo) % 4 == 0:              # palabras de 4 bytes al revés
            a = array.array("I", trozo)
            a.byteswap()
            trozo = a.tobytes()
        h.update(trozo)
    return h.hexdigest()


def _orden_n64(cabecera: bytes) -> str | None:
    return {b"\x37\x80\x40\x12": "v64", b"\x40\x12\x37\x80": "n64"}.get(cabecera[:4])


def _sha1_fichero(ruta: Path, sistema: str) -> str | None:
    """SHA-1 del contenido tal como lo listan No-Intro/Redump, o None si no se puede."""
    ext = ruta.suffix.lower()
    if ext not in VERIFICABLES:
        return None
    if ext == ".zip":
        with zipfile.ZipFile(ruta) as z:
            dentro = [i for i in z.infolist() if not i.is_dir()]
            if len(dentro) != 1 or Path(dentro[0].filename).suffix.lower() not in VERIFICABLES - {".zip"}:
                return None
            with z.open(dentro[0]) as f:
                orden = _orden_n64(f.read(4)) if sistema == "n64" else None
            with z.open(dentro[0]) as f:
                return _sha1_flujo(f, orden)
    with open(ruta, "rb") as f:
        orden = _orden_n64(f.read(4)) if sistema == "n64" else None
        f.seek(0)
        return _sha1_flujo(f, orden)


# ---------------- verificar un juego ----------------

def verificar(sistema: str, nombre: str) -> dict:
    ruta = config.ROOT / sistema / nombre
    if sistema not in DATS:
        return {"estado": "sin_base", "detalle": "Este sistema no tiene base de volcados."}
    _descargar_dat(sistema)
    b = base(sistema)
    if b is None:
        return {"estado": "sin_base", "detalle": "No se pudo descargar la base de volcados.", "reintentar": True}
    fuente = DATS[sistema][0]
    if ruta.is_dir():
        # Varios ficheros (PS1 cue+bin, multidisco): cada pista de datos tiene que estar en la base.
        pistas = [p for p in sorted(ruta.rglob("*")) if p.is_file() and p.suffix.lower() in (".bin", ".iso")]
        if not pistas:
            return {"estado": "sin_base", "detalle": "Formato que no se puede comprobar."}
        juegos = set()
        for p in pistas:
            sha1 = _sha1_fichero(p, sistema) or ""
            entrada = b["sha1"].get(sha1)
            if entrada is None:
                # La primera pista lleva el número de serie (SYSTEM.CNF).
                return _sin_coincidencia(b, fuente, sistema, pistas[0], sha1, f"la pista «{p.name}»",
                                         sum(x.stat().st_size for x in pistas))
            juegos.add(entrada["juego"])
        return {"estado": "verificado", "fuente": fuente, "nombre": " / ".join(sorted(juegos))}
    sha1 = _sha1_fichero(ruta, sistema)
    if sha1 is None:
        return {"estado": "sin_base", "detalle": f"El formato {ruta.suffix} no se puede comprobar (comprimido o índice)."}
    entrada = b["sha1"].get(sha1)
    if entrada:
        return {"estado": "verificado", "fuente": fuente, "nombre": entrada["juego"]}
    return _sin_coincidencia(b, fuente, sistema, ruta, sha1, "este fichero", ruta.stat().st_size)


def serie_de(cod, sistema: str) -> str | None:
    """La serie con la que la base de volcados indexa el juego (ver _serie_base)."""
    if not cod:
        return None
    if sistema in NINTENDO_DISCO:
        return cod[0].split(":", 1)[1][:4]
    return cod[1]


def _sin_coincidencia(b: dict, fuente: str, sistema: str, ruta: Path, sha1: str, que: str, tamano: int) -> dict:
    if sistema in DISCOS:
        cod = cabeceras.codigo(ruta, sistema)
        serie = serie_de(cod, sistema)
        oficiales = b["serie"].get(serie) if serie else None
        if oficiales:
            iguales = sorted(n for n, t in oficiales.items() if t == tamano)
            if iguales:
                return {"estado": "dudoso", "fuente": fuente, "nombre": iguales[0],
                        "detalle": f"{fuente} tiene un volcado de {serie} ({iguales[0]}) del MISMO tamaño y {que} no "
                                   "coincide: datos alterados, disco dañado (suele no arrancar) o imagen modificada "
                                   f"(traducción, parche). SHA-1: {sha1}."}
            nombre = sorted(oficiales)[0]
            return {"estado": "otro_volcado", "fuente": fuente, "nombre": nombre,
                    "detalle": f"Es {nombre}, pero no el volcado de {fuente}: otro tamaño (otro programa de volcado u "
                               "otro corte de pistas). Normalmente funciona."}
    return {"estado": "desconocido", "fuente": fuente,
            "detalle": f"No está en {fuente} (normal en romhacks, traducciones y homebrew)."}


# ---------------- ¿es un juego publicado? (aviso al compartir) ----------------

_HOMEBREW = re.compile(r"\b(Aftermarket|Homebrew|PD)\b", re.I)


def identificar_publicado(sistema: str, nombre: str, sha: str | None) -> str | None:
    """Si el contenido es (o modifica) un juego publicado según No-Intro/Redump,
    el aviso para quien lo va a compartir; si no, None. Por el CONTENIDO, nunca
    por el nombre. Solo AVISA: compartir lo decide el administrador (gestor.py).
    Bloqueante: puede calcular el SHA-1 de varios GB."""
    if sistema not in DATS:
        return None
    v = de(sha)
    if v is None:
        v = verificar(sistema, nombre)
        if sha and not v.get("reintentar"):
            _guardar(sha, v)
    oficial = v.get("nombre") or ""
    if v["estado"] in ("verificado", "dudoso", "otro_volcado") and oficial and not _HOMEBREW.search(oficial):
        return f"Su contenido coincide con «{oficial}» ({v.get('fuente') or 'bases de volcados'}), un juego publicado."
    b = base(sistema)
    ruta = config.ROOT / sistema / nombre
    if ruta.is_dir():
        pistas = sorted(p for p in ruta.rglob("*") if p.is_file() and p.suffix.lower() in (".bin", ".iso"))
        ruta = pistas[0] if pistas else None
    cod = cabeceras.codigo(ruta, sistema) if ruta else None
    if cod and b:
        serie = serie_de(cod, sistema)
        oficiales = sorted(n for n in b["serie"].get(serie, {}) if n and not _HOMEBREW.search(n))
        if oficiales:
            return (f"Su cabecera lleva el código «{serie}» de «{oficiales[0]}», un juego publicado: es ese juego o una "
                    "modificación suya (los ROM hacks y las traducciones contienen el juego original).")
    return None


# ---------------- en segundo plano ----------------

def pendientes() -> list[tuple[str, str, str]]:
    """(sha256, sistema, nombre) de los juegos sin verificar."""
    with _conectar() as con:
        filas = con.execute("SELECT h.ruta, h.sha FROM hashes h LEFT JOIN verificaciones v ON v.sha = h.sha "
                            "WHERE v.sha IS NULL").fetchall()
    return [(f["sha"], *f["ruta"].split("/", 1)) for f in filas]


def _guardar(sha: str, r: dict) -> None:
    with _conectar() as con:
        con.execute("INSERT OR REPLACE INTO verificaciones (sha, estado, fuente, nombre, detalle, fecha) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (sha, r["estado"], r.get("fuente", ""), r.get("nombre", ""), r.get("detalle", ""), time.time()))


def de(sha: str | None) -> dict | None:
    if not sha:
        return None
    with _conectar() as con:
        fila = con.execute("SELECT * FROM verificaciones WHERE sha = ?", (sha,)).fetchone()
    return dict(fila) if fila else None


def todas() -> dict[str, dict]:
    with _conectar() as con:
        return {f["sha"]: dict(f) for f in con.execute("SELECT * FROM verificaciones")}


def arrancar_hilo() -> None:
    def bucle():
        time.sleep(30)                      # que el hilo de hashes vaya primero
        while True:
            for sha, sistema, nombre in pendientes():
                try:
                    r = verificar(sistema, nombre)
                    if not r.get("reintentar"):
                        _guardar(sha, r)
                except (OSError, zipfile.BadZipFile) as e:
                    print(f"[verificacion] {sistema}/{nombre}: {e}", flush=True)
            time.sleep(300)
    threading.Thread(target=bucle, name="verificacion", daemon=True).start()
