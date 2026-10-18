"""
Fuentes externas: juegos cuya descarga oficial está en la web de su autor
(juegos fanmade publicados por su autor). La app los descarga DE ESE ENLACE,
así cuentan para las estadísticas del autor; la copia de seguridad de esta
librería (el gestor avisa de que se guarda) solo se usa si el enlace no
responde o no coincide.

- Al guardar un enlace, el servidor descarga el fichero UNA vez y lo compara
  con el juego: si es la versión actual, se guarda el enlace; si es una
  versión nueva del mismo juego (cabecera), entra como update (versiones.py);
  si no, se rechaza.
- También se puede crear un juego nuevo directamente desde un enlace (con su
  nombre, carátula e idiomas), sin subir nada: p. ej. un fan game. Si el
  nombre no lleva extensión se toma la del fichero que da el enlace. Los
  .zip de RPG Maker (y de PS1 con cue+bin) se descomprimen aquí como al
  subirlos; la app comprueba lo que baja del enlace con su SHA (sha_enlace).
- Cada 12 h se mira (sin descargar) si el fichero del enlace ha cambiado
  (ETag, Last-Modified, tamaño): el gestor avisa y el administrador decide
  si lo importa. Nunca se importa solo.
- Solo enlaces https a direcciones públicas (redsegura.py), y tienen que ser
  el fichero, no una página web.
"""

import asyncio
import hashlib
import re
import sqlite3
import time
from pathlib import Path

import httpx
from fastapi import HTTPException

import config
import redsegura
import versiones

MAX_BYTES = 8 * 1024 ** 3
CADA_COMPROBACION = 12 * 3600


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def inicializar() -> None:
    with _conectar() as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS externos (
                juego_id TEXT PRIMARY KEY, url TEXT NOT NULL, pagina TEXT NOT NULL DEFAULT '',
                etag TEXT, modificado TEXT, tamano INTEGER, comprobado REAL, estado TEXT NOT NULL DEFAULT 'ok',
                detalle TEXT NOT NULL DEFAULT ''
            )""")
        if "sha_enlace" not in {c[1] for c in con.execute("PRAGMA table_info(externos)")}:
            con.execute("ALTER TABLE externos ADD COLUMN sha_enlace TEXT")


def _validar_url(url: str, campo: str) -> str:
    url = (url or "").strip()
    if len(url) > 500 or not redsegura.url_publica(url):
        raise HTTPException(400, f"{campo}: tiene que ser una dirección https pública.")
    return url


def _juego_id(sistema: str, nombre: str) -> str | None:
    with versiones._conectar() as con:
        fila = con.execute("SELECT id FROM juegos WHERE ruta = ?", (f"{sistema}/{nombre}",)).fetchone()
    return fila["id"] if fila else None


async def _descargar(url: str, destino: Path) -> tuple[str, int, dict]:
    """Descarga `url` a `destino`: (sha256, tamaño, cabeceras HTTP)."""
    h, tamano = hashlib.sha256(), 0
    try:
        async with redsegura.cliente(timeout=httpx.Timeout(20, read=300)) as cliente:
            async with cliente.stream("GET", url) as r:
                if r.status_code != 200:
                    raise HTTPException(502, f"El enlace responde HTTP {r.status_code}.")
                if r.headers.get("content-type", "").split(";")[0].strip() in ("text/html", "application/xhtml+xml"):
                    raise HTTPException(400, "El enlace lleva a una página web, no al fichero: hace falta el enlace "
                                             "de descarga directa.")
                with open(destino, "wb") as salida:
                    async for trozo in r.aiter_bytes(1024 * 1024):
                        tamano += len(trozo)
                        if tamano > MAX_BYTES:
                            raise HTTPException(413, "El fichero del enlace es demasiado grande.")
                        h.update(trozo)
                        salida.write(trozo)
                cabeceras = {"etag": r.headers.get("etag"), "modificado": r.headers.get("last-modified"),
                             "nombre": _nombre_de_respuesta(r)}
    except httpx.HTTPError as e:
        raise HTTPException(502, f"No se pudo descargar el enlace: {e}") from None
    return h.hexdigest(), tamano, cabeceras


def _nombre_de_respuesta(r: httpx.Response) -> str:
    """Nombre del fichero según el servidor (Content-Disposition) o la URL final."""
    from urllib.parse import unquote, urlparse
    disp = r.headers.get("content-disposition", "")
    m = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", disp) or re.search(r'filename\s*=\s*"?([^";]+)"?', disp)
    nombre = unquote(m.group(1)) if m else unquote(urlparse(str(r.url)).path.rsplit("/", 1)[-1])
    return nombre.strip()[:200]


def _guardar(juego_id: str, url: str, pagina: str, tamano: int, cab: dict, sha_enlace: str | None = None) -> None:
    with _conectar() as con:
        con.execute("INSERT OR REPLACE INTO externos (juego_id, url, pagina, etag, modificado, tamano, comprobado, estado, "
                    "detalle, sha_enlace) VALUES (?, ?, ?, ?, ?, ?, ?, 'ok', '', ?)",
                    (juego_id, url, pagina, cab.get("etag"), cab.get("modificado"), tamano, time.time(), sha_enlace))


async def guardar_enlace(sistema: str, nombre: str, url: str, pagina: str, usuario: str, sin_cabecera_ok: bool) -> dict:
    """Enlace para un juego que ya está en la librería (ver cabecera del módulo)."""
    url = _validar_url(url, "Enlace")
    pagina = _validar_url(pagina, "Página del autor") if pagina.strip() else ""
    _, _, ruta, juego = versiones._juego_actualizable(sistema, nombre, Path(nombre).suffix)
    temporal = versiones.temporal_junto_a(ruta)
    try:
        sha, tamano, cab = await _descargar(url, temporal)
        actual = versiones.historial(ruta.parent.name, ruta.name)["versiones"][0]
        if sha == actual["sha"]:
            resultado = {"ok": True, "version": actual["n"], "nueva": False}
        else:
            r = await versiones.aplicar(ruta.parent.name, ruta.name, temporal, sha, tamano,
                                        f"{usuario} (enlace externo)", sin_cabecera_ok)
            resultado = {"ok": True, "version": r["version"], "nueva": True}
        _guardar(juego["id"], url, pagina, tamano, cab)
        return resultado
    finally:
        temporal.unlink(missing_ok=True)


def _nombre_final(sistema: str, nombre: str, nombre_enlace: str) -> str:
    """El nombre que da el administrador, con extensión: la suya si es válida
    para la consola; si no, la del fichero del enlace."""
    import main as nucleo
    extensiones = nucleo.EXTENSIONES_POR_SISTEMA[sistema]
    if any(nombre.lower().endswith(e) for e in extensiones):
        return nombre
    ext = Path(nombre_enlace).suffix.lower()
    if ext in extensiones:
        return nombre + ext
    raise HTTPException(400, f"No se sabe qué tipo de fichero da el enlace («{nombre_enlace or 'sin nombre'}»): escribe "
                             f"el nombre con su extensión ({', '.join(sorted(extensiones))}).")


async def anadir_juego(sistema: str, nombre: str, url: str, pagina: str, usuario: str, caratula=None,
                       idiomas=None, titulo: str = "") -> dict:
    """Juego nuevo creado desde un enlace (se guarda copia de respaldo aquí)."""
    import federacion
    import fichas
    import main as nucleo
    from starlette.datastructures import UploadFile
    url = _validar_url(url, "Enlace")
    pagina = _validar_url(pagina, "Página del autor") if pagina.strip() else ""
    sistema, nombre = nucleo.nombre_seguro(sistema), " ".join(nombre.split())
    if sistema not in nucleo.EXTENSIONES_POR_SISTEMA:
        raise HTTPException(400, "Consola desconocida.")
    if not nombre:
        raise HTTPException(400, "Falta el nombre del juego.")
    nucleo.nombre_seguro(nombre)
    carpeta = config.ROOT / sistema
    carpeta.mkdir(parents=True, exist_ok=True)
    temporal = versiones.temporal_junto_a(carpeta / nombre)
    try:
        sha, tamano, cab = await _descargar(url, temporal)
        nombre = nucleo.nombre_seguro(_nombre_final(sistema, nombre, cab.get("nombre") or ""))
        with versiones._conectar() as con:
            conocida = con.execute("SELECT j.ruta FROM versiones v JOIN juegos j ON j.id = v.juego_id WHERE v.sha = ?",
                                   (sha,)).fetchone()
        if conocida is not None:
            raise HTTPException(409, f"Ese fichero ya está en la librería como «{(conocida['ruta'] or 'un juego borrado').split('/', 1)[-1]}».")
        # Mismo camino que una subida desde el gestor (descomprime RPG Maker y PS1, valida y guarda la carátula).
        with open(temporal, "rb") as f:
            r = await nucleo.subir_juego(nucleo.PETICION_INTERNA, nucleo.TOKEN_SISTEMA, system=sistema, filename=nombre,
                                         rom=UploadFile(f, filename=nombre), cover=caratula)
        final = r["filename"]
        ruta = carpeta / final
        sha_juego = sha if ruta.is_file() else await asyncio.to_thread(federacion._sha_juego, ruta)
        tam_juego, mtime = federacion._firma(ruta)
        with versiones._conectar() as con:
            con.execute("INSERT OR REPLACE INTO hashes (ruta, tamano, mtime, sha) VALUES (?, ?, ?, ?)",
                        (f"{sistema}/{final}", tam_juego, mtime, sha_juego))
        await asyncio.to_thread(versiones.registrar, sistema, final, sha_juego, tam_juego)
        _guardar(_juego_id(sistema, final), url, pagina, tamano, cab, sha if sha != sha_juego else None)
        fichas.guardar(sistema, final, usuario, titulo=titulo, idiomas=idiomas or [])
        return {"ok": True, "filename": final, "sha": sha_juego}
    finally:
        temporal.unlink(missing_ok=True)


def quitar(sistema: str, nombre: str) -> None:
    juego_id = _juego_id(sistema, nombre)
    if juego_id:
        with _conectar() as con:
            con.execute("DELETE FROM externos WHERE juego_id = ?", (juego_id,))


def info(sistema: str, nombre: str) -> dict | None:
    juego_id = _juego_id(sistema, nombre)
    if not juego_id:
        return None
    with _conectar() as con:
        fila = con.execute("SELECT * FROM externos WHERE juego_id = ?", (juego_id,)).fetchone()
    return dict(fila) if fila else None


def por_ruta() -> dict[str, dict]:
    """ruta -> {url, pagina, estado, sha_enlace} de los juegos con fuente externa."""
    with _conectar() as con:
        filas = con.execute("SELECT j.ruta, e.url, e.pagina, e.estado, e.sha_enlace FROM externos e JOIN juegos j ON j.id = e.juego_id "
                            "WHERE j.ruta IS NOT NULL").fetchall()
    return {f["ruta"]: {"url": f["url"], "pagina": f["pagina"], "estado": f["estado"], "sha_enlace": f["sha_enlace"]}
            for f in filas}


# ---------------- comprobación periódica (sin descargar) ----------------

async def comprobar_todos() -> None:
    with _conectar() as con:
        filas = con.execute("SELECT * FROM externos").fetchall()
    async with redsegura.cliente(timeout=20) as cliente:
        for f in filas:
            try:
                r = await cliente.head(f["url"])
                if r.status_code != 200:
                    estado, detalle = "caido", f"HTTP {r.status_code}"
                else:
                    tam = r.headers.get("content-length")
                    cambios = [n for n, viejo, nuevo in (("ETag", f["etag"], r.headers.get("etag")),
                                                         ("fecha", f["modificado"], r.headers.get("last-modified")),
                                                         ("tamaño", str(f["tamano"]), tam))
                               if viejo and nuevo and viejo != nuevo]
                    estado, detalle = ("cambiado", "Cambia: " + ", ".join(cambios)) if cambios else ("ok", "")
            except httpx.HTTPError as e:
                estado, detalle = "caido", str(e)[:200]
            with _conectar() as con:
                con.execute("UPDATE externos SET estado = ?, detalle = ?, comprobado = ? WHERE juego_id = ?",
                            (estado, detalle, time.time(), f["juego_id"]))


async def bucle() -> None:
    await asyncio.sleep(120)
    while True:
        try:
            await comprobar_todos()
        except Exception as e:  # noqa: BLE001 -- el bucle no puede morir
            print(f"[externos] {e}", flush=True)
        await asyncio.sleep(CADA_COMPROBACION)
