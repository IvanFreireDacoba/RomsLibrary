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

Los juegos que ya existían entran solos como v1 cuando el hilo de hashes
calcula su SHA (federacion.py). Si un fichero se renombra, su SHA ya conocido
lo vuelve a enlazar con su juego.
"""

import hashlib
import os
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
            """
        )


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
    with _conectar() as con:
        con.execute("UPDATE juegos SET ruta = ? WHERE ruta = ?", (f"{sistema}/{nuevo}", f"{sistema}/{viejo}"))
        if mover_hash:
            con.execute("UPDATE OR REPLACE hashes SET ruta = ? WHERE ruta = ?", (f"{sistema}/{nuevo}", f"{sistema}/{viejo}"))


def _anadir_version(con, juego_id: str, sha: str, tamano: int, usuario: str, codigo: str | None) -> int:
    actual = _actual(con, juego_id)
    n = (actual["n"] if actual else 0) + 1
    con.execute("INSERT INTO versiones (sha, juego_id, n, tamano, fecha, usuario, codigo) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sha, juego_id, n, tamano, time.time(), usuario, codigo))
    return n


# ---------------- consultas ----------------

def historial(sistema: str, nombre: str) -> dict | None:
    with _conectar() as con:
        juego = con.execute("SELECT * FROM juegos WHERE ruta = ?", (f"{sistema}/{nombre}",)).fetchone()
        if juego is None:
            return None
        filas = con.execute("SELECT * FROM versiones WHERE juego_id = ? ORDER BY n DESC", (juego["id"],)).fetchall()
    return {"id": juego["id"], "codigo": juego["codigo"], "titulo": juego["titulo"],
            "versiones": [{"n": f["n"], "sha": f["sha"], "tamano": f["tamano"], "fecha": f["fecha"],
                           "usuario": f["usuario"]} for f in filas]}


def cadenas_propias() -> dict[str, dict]:
    """ruta -> {"codigo", "cadena": [(sha, tamano), ...] de la más vieja a la actual}."""
    with _conectar() as con:
        filas = con.execute("SELECT j.ruta, j.codigo, v.sha, v.tamano FROM juegos j JOIN versiones v ON v.juego_id = j.id "
                            "WHERE j.ruta IS NOT NULL ORDER BY j.ruta, v.n").fetchall()
    res: dict[str, dict] = {}
    for f in filas:
        res.setdefault(f["ruta"], {"codigo": f["codigo"], "cadena": []})["cadena"].append((f["sha"], f["tamano"]))
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
        return await aplicar(sistema, nombre, temporal, h.hexdigest(), tamano, usuario, sin_cabecera_ok)
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
