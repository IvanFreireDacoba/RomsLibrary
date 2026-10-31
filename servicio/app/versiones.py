"""
Versiones de los juegos. La identidad de un juego NO es su nombre (lo pone
quien sube y se puede cambiar) sino su CADENA DE VERSIONES: cada juego tiene
un id fijo y la lista de los SHA-256 que ha tenido (v1, v2, v3...). La versión
actual es la última; las anteriores sirven para que las consolas que tengan
una vieja sepan a qué juego pertenece y se actualicen (ver accion=versiones en
api_app.py y ActualizadorRoms en la app).

Reglas:
- Un SHA pertenece a UN solo juego (clave primaria): no puede ser a la vez
  versión de dos juegos distintos.
- Una actualización solo se acepta si el código de la CABECERA del ROM nuevo
  coincide con el del juego (cabeceras.py). Si el formato no tiene cabecera
  legible, el gestor tiene que confirmarlo expresamente.
- Nunca se vuelve a una versión anterior ni se ordena por fechas: el orden es
  el de la cadena.
- El fichero conserva su nombre al actualizarse: así la app lo deja con el
  mismo nombre en la consola y los emuladores siguen encontrando las partidas.
- Al actualizar se guarda aparte la versión que se sustituye (si no estaba
  confirmada como corrupta): si la nueva resulta corrupta, se sirve esa
  (federacion.juegos_propios). Solo una por juego, y se borra cuando la
  actual queda verificada (No-Intro/Redump o «está bien» del administrador).

Los juegos que ya existían entran solos como v1 cuando el hilo de hashes
calcula su SHA (federacion.py). Si un fichero se renombra, su SHA ya conocido
lo vuelve a enlazar con su juego.
"""

import hashlib
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

from fastapi import HTTPException

import cabeceras
import config


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def inicializar() -> None:
    with _conectar() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS juegos (
                id TEXT PRIMARY KEY, sistema TEXT NOT NULL, ruta TEXT UNIQUE,
                codigo TEXT, titulo TEXT, creado REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS versiones (
                sha TEXT PRIMARY KEY, juego_id TEXT NOT NULL, n INTEGER NOT NULL,
                tamano INTEGER NOT NULL, fecha REAL NOT NULL, usuario TEXT NOT NULL DEFAULT '',
                codigo TEXT
            );
            CREATE INDEX IF NOT EXISTS versiones_juego ON versiones (juego_id, n);
            CREATE TABLE IF NOT EXISTS anterior_limpia (
                juego_id TEXT PRIMARY KEY, sha TEXT NOT NULL, n INTEGER NOT NULL, tamano INTEGER NOT NULL
            );
            """
        )
        # Etiqueta vX.Y.Z editable a mano por el administrador (cosmética: el
        # orden y la detección de updates siguen siendo por `n`/SHA). Las
        # versiones que ya había muestran «v{n}» hasta que se les ponga una.
        if "etiqueta" not in {c[1] for c in con.execute("PRAGMA table_info(versiones)")}:
            con.execute("ALTER TABLE versiones ADD COLUMN etiqueta TEXT")
    ANTERIORES.mkdir(parents=True, exist_ok=True)


# Última versión limpia de cada juego, guardada al actualizarlo (ver arriba).
ANTERIORES = config.DATOS / "anteriores"


def _actual(con, juego_id: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM versiones WHERE juego_id = ? ORDER BY n DESC LIMIT 1", (juego_id,)).fetchone()


def _codigo_de(ruta: Path, sistema: str):
    if not ruta.is_file():
        return None                                     # grupos de varios ficheros: sin código por ahora
    return cabeceras.codigo(ruta, sistema)


# ---------------- registro automático (hilo de hashes) ----------------

def registrar(sistema: str, nombre: str, sha: str, tamano: int) -> None:
    """El hilo de hashes acaba de calcular (o confirmar) el SHA de un fichero."""
    ruta_rel = f"{sistema}/{nombre}"
    with _conectar() as con:
        juego = con.execute("SELECT * FROM juegos WHERE ruta = ?", (ruta_rel,)).fetchone()
        conocida = con.execute("SELECT * FROM versiones WHERE sha = ?", (sha,)).fetchone()
        if juego is not None:
            actual = _actual(con, juego["id"])
            if actual is not None and actual["sha"] == sha:
                return
            if conocida is not None and conocida["juego_id"] == juego["id"]:
                return                                  # ya registrada (p. ej. justo después de actualizar)
            # El fichero ha cambiado por fuera del gestor (copia a mano): solo
            # cuenta como versión nueva si la cabecera sigue siendo del mismo juego.
            cod = _codigo_de(config.ROOT / sistema / nombre, sistema)
            if conocida is None and cod is not None and cod[0] == juego["codigo"]:
                _anadir_version(con, juego["id"], sha, tamano, "cambio fuera del gestor", cod[0])
                return
            con.execute("UPDATE juegos SET ruta = NULL WHERE id = ?", (juego["id"],))
        if conocida is not None:
            # SHA conocido en otra ruta: el juego se ha renombrado o movido.
            con.execute("UPDATE juegos SET ruta = NULL WHERE ruta = ?", (ruta_rel,))
            con.execute("UPDATE juegos SET ruta = ? WHERE id = ?", (ruta_rel, conocida["juego_id"]))
            return
        cod = _codigo_de(config.ROOT / sistema / nombre, sistema)
        juego_id = uuid.uuid4().hex
        con.execute("INSERT INTO juegos (id, sistema, ruta, codigo, titulo, creado) VALUES (?, ?, ?, ?, ?, ?)",
                    (juego_id, sistema, ruta_rel, cod[0] if cod else None, cod[1] if cod else None, time.time()))
        _anadir_version(con, juego_id, sha, tamano, "", cod[0] if cod else None)


def olvidar_ruta(ruta_rel: str) -> None:
    """El fichero ya no está (borrado): el juego conserva su historial sin ruta."""
    with _conectar() as con:
        con.execute("UPDATE juegos SET ruta = NULL WHERE ruta = ?", (ruta_rel,))


def renombrado(sistema: str, viejo: str, nuevo: str, mover_hash: bool) -> None:
    import fichas
    fichas.renombrar(sistema, viejo, nuevo)
    with _conectar() as con:
        con.execute("UPDATE juegos SET ruta = ? WHERE ruta = ?", (f"{sistema}/{nuevo}", f"{sistema}/{viejo}"))
        if mover_hash:
            con.execute("UPDATE OR REPLACE hashes SET ruta = ? WHERE ruta = ?", (f"{sistema}/{nuevo}", f"{sistema}/{viejo}"))


def _anadir_version(con, juego_id: str, sha: str, tamano: int, usuario: str, codigo: str | None) -> int:
    actual = _actual(con, juego_id)
    n = (actual["n"] if actual else 0) + 1
    con.execute("INSERT INTO versiones (sha, juego_id, n, tamano, fecha, usuario, codigo, etiqueta) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sha, juego_id, n, tamano, time.time(), usuario, codigo, etiqueta_por_defecto(n)))
    return n


# ---------------- etiqueta vX.Y.Z (editable) ----------------

# vX.Y.Z con un sufijo opcional «-texto» / «+texto» (p. ej. v3.0.6-Spanish,
# v2.1.0-Español). El sufijo admite letras (con acentos), cifras, espacios y
# unos pocos signos, pero no puede ir vacío ni empezar por espacio.
_ETIQUETA_RE = re.compile(r"^v?\d{1,4}\.\d{1,4}\.\d{1,4}(?:[-+][0-9A-Za-zÀ-ÿ][0-9A-Za-zÀ-ÿ._\-() ]{0,39})?$")


def etiqueta_por_defecto(n: int) -> str:
    """Etiqueta inicial de una versión nueva: el administrador la edita luego."""
    return f"v{n}.0.0"


def normalizar_etiqueta(etiqueta: str) -> str:
    etiqueta = (etiqueta or "").strip()
    if not etiqueta or len(etiqueta) > 60 or not _ETIQUETA_RE.match(etiqueta):
        raise HTTPException(400, "La etiqueta tiene que ser del tipo vX.Y.Z, opcionalmente con «-texto» al final "
                                 "(por ejemplo v3.0.6-Spanish).")
    return etiqueta if etiqueta[0] in "vV" else "v" + etiqueta


def fijar_etiqueta(sistema: str, nombre: str, n: int, etiqueta: str) -> dict:
    """El gestor pone/cambia la etiqueta vX.Y.Z de una versión concreta del juego."""
    etiqueta = normalizar_etiqueta(etiqueta)
    with _conectar() as con:
        juego = con.execute("SELECT id FROM juegos WHERE ruta = ?", (f"{sistema}/{nombre}",)).fetchone()
        if juego is None:
            raise HTTPException(404, "Este juego aún no tiene versiones registradas.")
        cur = con.execute("UPDATE versiones SET etiqueta = ? WHERE juego_id = ? AND n = ?", (etiqueta, juego["id"], n))
        if cur.rowcount == 0:
            raise HTTPException(404, f"Este juego no tiene una versión v{n}.")
    return {"n": n, "etiqueta": etiqueta}


# ---------------- consultas ----------------

def historial(sistema: str, nombre: str) -> dict | None:
    with _conectar() as con:
        juego = con.execute("SELECT * FROM juegos WHERE ruta = ?", (f"{sistema}/{nombre}",)).fetchone()
        if juego is None:
            return None
        filas = con.execute("SELECT * FROM versiones WHERE juego_id = ? ORDER BY n DESC", (juego["id"],)).fetchall()
    return {"id": juego["id"], "codigo": juego["codigo"], "titulo": juego["titulo"],
            "versiones": [{"n": f["n"], "sha": f["sha"], "tamano": f["tamano"], "fecha": f["fecha"],
                           "usuario": f["usuario"], "etiqueta": f["etiqueta"] or f"v{f['n']}"} for f in filas]}


def cadenas_propias() -> dict[str, dict]:
    """ruta -> {"codigo", "etiqueta" (la de la versión actual), "cadena": [(sha, tamano), ...]
    de la más vieja a la actual}."""
    with _conectar() as con:
        filas = con.execute("SELECT j.ruta, j.codigo, v.sha, v.tamano, v.n, v.etiqueta "
                            "FROM juegos j JOIN versiones v ON v.juego_id = j.id "
                            "WHERE j.ruta IS NOT NULL ORDER BY j.ruta, v.n").fetchall()
    res: dict[str, dict] = {}
    for f in filas:
        d = res.setdefault(f["ruta"], {"codigo": f["codigo"], "etiqueta": None, "cadena": []})
        d["cadena"].append((f["sha"], f["tamano"]))
        d["etiqueta"] = f["etiqueta"] or f"v{f['n']}"      # la última fila (n mayor) = la actual
    return res


def numero_actual(sistema: str, nombre: str) -> int | None:
    with _conectar() as con:
        juego = con.execute("SELECT id FROM juegos WHERE ruta = ?", (f"{sistema}/{nombre}",)).fetchone()
        actual = _actual(con, juego["id"]) if juego else None
    return actual["n"] if actual else None


# ---------------- actualizar desde el gestor ----------------

def _sha_fichero(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        while trozo := f.read(4 * 1024 * 1024):
            h.update(trozo)
    return h.hexdigest()


# Extensión del ROM crudo dentro del .zip, por sistema (solo cosmética: RetroArch
# carga el .zip por su contenido, no por el nombre interno).
_EXT_CRUDA = {"gb": ".gb", "gbc": ".gbc", "gba": ".gba", "n64": ".z64"}


def _normalizar_contenedor(temporal: Path, ruta: Path, sistema: str) -> Path:
    """Deja el fichero subido en el MISMO contenedor con el que se guarda el
    juego (la extensión de `ruta`): así una versión nueva se valida por su
    CONTENIDO y no por cómo llegue envuelta. Caso soportado: el juego se guarda
    en `.zip` (como lo acepta RetroArch) y lo subido es un cartucho crudo del
    mismo sistema -> se mete en un `.zip`. Devuelve la ruta a usar: un temporal
    nuevo si normaliza (y borra el viejo), o el mismo `temporal` si no toca."""
    import zipfile
    if ruta.suffix.lower() != ".zip" or cabeceras.es_zip(temporal):
        return temporal
    if cabeceras.codigo_crudo(temporal, sistema) is None:
        return temporal                       # no es un cartucho reconocible: que lo rechace aplicar()
    ext = _EXT_CRUDA.get(sistema, "")
    nuevo = temporal.with_name(temporal.name + ".zip")
    with zipfile.ZipFile(nuevo, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(temporal, arcname=f"{ruta.stem}{ext}")
    temporal.unlink(missing_ok=True)
    return nuevo


def limpiar_zip_cartucho(ruta: Path) -> bool:
    """Si `ruta` es un .zip con UNA sola ROM de cartucho y además basura dentro
    (carpetas, readmes, restos de macOS __MACOSX/ y ._*), lo reescribe dejando
    SOLO la ROM. Si no, RetroArch puede cargar el fichero equivocado del zip
    -p. ej. el ._fichero de 220 bytes del resource-fork de macOS en vez de la
    ROM real- y cerrarse (caso real "Pokémon Team Rocket" subido como zip con
    106 ficheros). Devuelve True si tocó el fichero. No toca zips con cero o
    varias ROMs (ambiguo: que decida aplicar/registrar)."""
    import zipfile
    if ruta.suffix.lower() != ".zip" or not cabeceras.es_zip(ruta):
        return False
    roms = cabeceras.roms_en_zip(ruta)
    if len(roms) != 1:
        return False
    with zipfile.ZipFile(ruta) as z:
        ficheros = [i.filename for i in z.infolist() if not i.is_dir()]
        if ficheros == roms:
            return False                      # ya está limpio: la ROM es el único fichero
        datos = z.read(roms[0])
    tmp = ruta.with_name(ruta.name + ".limpio")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(Path(roms[0]).name, datos)   # aplana subcarpetas ("Carpeta/rom.gba" -> "rom.gba")
    tmp.replace(ruta)                            # mismo disco: atómico
    return True


def _juego_actualizable(sistema: str, nombre: str, extension_nueva: str):
    import main as nucleo
    sistema, nombre = nucleo.nombre_seguro(sistema), nucleo.nombre_seguro(nombre)
    ruta = config.ROOT / sistema / nombre
    if not ruta.is_file():
        raise HTTPException(400, "Solo se pueden actualizar los juegos de un solo fichero (los de varios, todavía no).")
    if extension_nueva.lower() != ruta.suffix.lower():
        raise HTTPException(400, f"La versión nueva tiene que ser también {ruta.suffix}: el nombre del juego en las "
                                 "consolas no cambia (así siguen valiendo las partidas).")
    with _conectar() as con:
        juego = con.execute("SELECT * FROM juegos WHERE ruta = ?", (f"{sistema}/{nombre}",)).fetchone()
    if juego is None:
        raise HTTPException(409, "Este juego aún no tiene versión registrada (se está calculando su SHA). "
                                 "Prueba en unos minutos.")
    return sistema, nombre, ruta, juego


def temporal_junto_a(ruta: Path) -> Path:
    """Temporal en la misma carpeta (mismo disco: el cambio final es atómico)."""
    return ruta.parent / f".actualizacion-{uuid.uuid4().hex}.tmp"


async def actualizar(sistema: str, nombre: str, rom, usuario: str, sin_cabecera_ok: bool) -> dict:
    """Sustituye el ROM de un juego por su versión nueva subida desde el gestor."""
    sistema, nombre, ruta, juego = _juego_actualizable(sistema, nombre, Path(rom.filename or "").suffix)
    temporal = temporal_junto_a(ruta)
    h, tamano = hashlib.sha256(), 0
    try:
        with open(temporal, "wb") as salida:
            while trozo := await rom.read(4 * 1024 * 1024):
                h.update(trozo)
                tamano += len(trozo)
                salida.write(trozo)
        sha = h.hexdigest()
        nuevo = _normalizar_contenedor(temporal, ruta, sistema)
        if nuevo is not temporal:
            temporal, sha, tamano = nuevo, _sha_fichero(nuevo), nuevo.stat().st_size
        return await aplicar(sistema, nombre, temporal, sha, tamano, usuario, sin_cabecera_ok)
    finally:
        temporal.unlink(missing_ok=True)


async def aplicar(sistema: str, nombre: str, temporal: Path, sha: str, tamano: int, usuario: str,
                  sin_cabecera_ok: bool) -> dict:
    """Comprueba que `temporal` (ya en disco, misma carpeta) es una versión
    nueva del juego y, si lo es, lo pone en su sitio. Si no, HTTPException."""
    import asyncio
    sistema, nombre, ruta, juego = _juego_actualizable(sistema, nombre, Path(nombre).suffix)
    with _conectar() as con:
        actual = _actual(con, juego["id"])
        conocida = con.execute("SELECT v.*, j.ruta FROM versiones v JOIN juegos j ON j.id = v.juego_id "
                               "WHERE v.sha = ?", (sha,)).fetchone()
    if conocida is not None:
        if conocida["juego_id"] != juego["id"]:
            otro = (conocida["ruta"] or "un juego borrado").split("/", 1)[-1]
            raise HTTPException(409, f"Ese fichero ya está en la librería como «{otro}»: no puede ser una versión de este.")
        if conocida["n"] == actual["n"]:
            raise HTTPException(409, "Ese fichero ya es la versión actual.")
        raise HTTPException(409, f"Ese fichero es una versión ANTERIOR de este juego (v{conocida['n']}): "
                                 "no se vuelve atrás.")
    cod_nuevo = await asyncio.to_thread(cabeceras.codigo, temporal, sistema, nombre)
    cod_viejo = juego["codigo"]
    if cod_nuevo and cod_viejo and cod_nuevo[0] != cod_viejo:
        raise HTTPException(409, f"No es el mismo juego: la cabecera del ROM actual dice «{cod_viejo}» y la del "
                                 f"nuevo «{cod_nuevo[0]}» ({cod_nuevo[1]}).")
    if cod_viejo and not cod_nuevo:
        # El juego tiene cabecera legible: lo nuevo también tiene que tenerla
        # (y la misma). Esto no se puede saltar confirmando.
        raise HTTPException(409, f"El fichero nuevo no tiene una cabecera válida de {sistema.upper()}: no parece una "
                                 f"versión de este juego («{cod_viejo}»).")
    if not cod_viejo and not sin_cabecera_ok:
        raise HTTPException(428, "Este formato no tiene una cabecera que se pueda comprobar: confirma que es "
                                 "de verdad una versión nueva de este mismo juego.")
    _guardar_anterior(sistema, nombre, ruta, juego["id"], actual)
    os.replace(temporal, ruta)
    st = ruta.stat()
    with _conectar() as con:
        n = _anadir_version(con, juego["id"], sha, tamano, usuario, cod_nuevo[0] if cod_nuevo else None)
        if cod_nuevo and not cod_viejo:
            con.execute("UPDATE juegos SET codigo = ?, titulo = ? WHERE id = ?", (cod_nuevo[0], cod_nuevo[1], juego["id"]))
        # El hilo de hashes no tiene que volver a leer el fichero entero.
        con.execute("INSERT OR REPLACE INTO hashes (ruta, tamano, mtime, sha) VALUES (?, ?, ?, ?)",
                    (f"{sistema}/{nombre}", st.st_size, st.st_mtime, sha))
    return {"ok": True, "version": n, "sha": sha}


# ---------------- versión limpia anterior ----------------

def _guardar_anterior(sistema: str, nombre: str, ruta: Path, juego_id: str, actual) -> None:
    """Antes de sustituir el fichero: si la versión actual no está confirmada
    como corrupta, pasa a ser la «anterior limpia» del juego (en lugar de la
    que hubiera). Si lo está, se conserva la que ya había."""
    import fichas
    if actual is None or fichas.confirmado_corrupto(sistema, nombre, actual["sha"]):
        return
    with _conectar() as con:
        vieja = con.execute("SELECT sha FROM anterior_limpia WHERE juego_id = ?", (juego_id,)).fetchone()
        con.execute("INSERT OR REPLACE INTO anterior_limpia (juego_id, sha, n, tamano) VALUES (?, ?, ?, ?)",
                    (juego_id, actual["sha"], actual["n"], actual["tamano"]))
    os.replace(ruta, ANTERIORES / actual["sha"])          # mismo disco (/datos): instantáneo
    if vieja and vieja["sha"] != actual["sha"]:
        (ANTERIORES / vieja["sha"]).unlink(missing_ok=True)


def anterior_limpia(sistema: str, nombre: str) -> dict | None:
    """{sha, n, tamano, fichero} de la versión limpia guardada de ese juego, si la hay."""
    with _conectar() as con:
        fila = con.execute("SELECT a.* FROM anterior_limpia a JOIN juegos j ON j.id = a.juego_id WHERE j.ruta = ?",
                           (f"{sistema}/{nombre}",)).fetchone()
    if fila is None or not (ANTERIORES / fila["sha"]).is_file():
        return None
    return {"sha": fila["sha"], "n": fila["n"], "tamano": fila["tamano"], "fichero": ANTERIORES / fila["sha"]}


def limpiar_anteriores() -> None:
    """Hilo de hashes: borra las anteriores de juegos borrados y las de juegos
    cuya versión actual ya está verificada como buena."""
    import fichas
    import verificacion
    with _conectar() as con:
        filas = con.execute("SELECT a.juego_id, a.sha, j.ruta, (SELECT sha FROM versiones v WHERE v.juego_id = a.juego_id "
                            "ORDER BY n DESC LIMIT 1) AS actual FROM anterior_limpia a "
                            "LEFT JOIN juegos j ON j.id = a.juego_id").fetchall()
    for f in filas:
        sobra = f["ruta"] is None
        if not sobra:
            verif = verificacion.de(f["actual"])
            sistema, nombre = f["ruta"].split("/", 1)
            sobra = (verif or {}).get("estado") == "ok" or fichas.confirmado_bueno(sistema, nombre, f["actual"])
        if sobra:
            with _conectar() as con:
                con.execute("DELETE FROM anterior_limpia WHERE juego_id = ?", (f["juego_id"],))
            (ANTERIORES / f["sha"]).unlink(missing_ok=True)
    with _conectar() as con:
        conocidas = {f["sha"] for f in con.execute("SELECT sha FROM anterior_limpia").fetchall()}
    for fichero in ANTERIORES.iterdir():
        # Restos de una actualización interrumpida (el ctime cambia al moverlo
        # aquí: así nunca se borra uno que se está guardando ahora mismo).
        if fichero.name not in conocidas and time.time() - fichero.stat().st_ctime > 3600:
            fichero.unlink(missing_ok=True)
