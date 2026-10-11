"""
Federación de librerías públicas de RomsLibrary.

Cada servicio es PÚBLICO (comparte sus juegos y ve los de las demás
librerías públicas) o PRIVADO (solo los suyos). Un servicio hace de ÍNDICE
(sund.es): guarda la lista de librerías públicas.

Alta en el índice (prueba de que controlas la URL que declaras): el servicio
genera un código de un solo uso, lo guarda como "alta pendiente" y se lo
manda al índice junto a su URL; el índice pide GET <url>/federacion/ping y
solo da de alta si esa URL le devuelve el mismo código. Así nadie puede
registrar la URL de otro. El índice devuelve un id + secreto (solo se
guarda el hash del secreto).

Entre librerías nunca viaja ese secreto: para leer el catálogo o los
ficheros de otra librería se pide al índice un TICKET (10 min, solo para esa
librería de destino) y se manda en X-RomsLibrary-Ticket; la librería que lo
recibe lo valida contra el índice (y lo cachea mientras dure).

Sin duplicados: cada juego tiene un SHA-256 (fichero suelto) o el SHA-256
de la lista ordenada "ruta:sha256" de sus ficheros (juegos de varios
ficheros). Si dos librerías tienen el mismo juego se muestra una sola vez,
con preferencia por el de la librería personal.

Los juegos de otras librerías nunca se pueden editar desde esta: solo
descargar (este servicio hace de intermediario) o COPIAR a la librería
personal (se bajan los ficheros y pasan a ser propios).
"""

import asyncio
import hashlib
import secrets
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

import ajustes
import config

router = APIRouter(prefix="/federacion")

DURACION_TICKET = 600
CACHE_CATALOGOS = 600
TIMEOUT_REMOTO = httpx.Timeout(10, read=60)


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def inicializar() -> None:
    with _conectar() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS hashes (
                ruta TEXT PRIMARY KEY, tamano INTEGER NOT NULL, mtime REAL NOT NULL, sha TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS indice_servicios (
                id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, nombre TEXT NOT NULL,
                secreto_hash TEXT NOT NULL, publico INTEGER NOT NULL DEFAULT 1,
                alta REAL NOT NULL, ultimo_contacto REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS indice_tickets (
                ticket TEXT PRIMARY KEY, origen_id TEXT NOT NULL, destino_id TEXT NOT NULL, caduca REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS copias (
                id INTEGER PRIMARY KEY, origen TEXT NOT NULL, sistema TEXT NOT NULL, fichero TEXT NOT NULL,
                nuevo_nombre TEXT NOT NULL, estado TEXT NOT NULL, detalle TEXT NOT NULL DEFAULT '',
                bytes_hechos INTEGER NOT NULL DEFAULT 0, bytes_total INTEGER NOT NULL DEFAULT 0,
                creada REAL NOT NULL
            );
            """
        )
        # Copias que estaban a medias cuando se paró el servicio.
        con.execute("UPDATE copias SET estado = 'error', detalle = 'Interrumpida al reiniciar el servicio' "
                    "WHERE estado IN ('pendiente', 'copiando')")


def _hash_secreto(secreto: str) -> str:
    return hashlib.sha256(secreto.encode()).hexdigest()


# =====================================================================
# Hashes de los juegos propios (en segundo plano, cacheados por tamaño+mtime)
# =====================================================================

def _firma(ruta: Path) -> tuple[int, float]:
    if ruta.is_dir():
        ficheros = [p for p in ruta.rglob("*") if p.is_file() and p.name != "grupo.json"]
        return sum(p.stat().st_size for p in ficheros), max((p.stat().st_mtime for p in ficheros), default=0)
    st = ruta.stat()
    return st.st_size, st.st_mtime


def _sha_fichero(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        while trozo := f.read(4 * 1024 * 1024):
            h.update(trozo)
    return h.hexdigest()


def _sha_juego(ruta: Path) -> str:
    if ruta.is_file():
        return _sha_fichero(ruta)
    lineas = sorted(f"{p.relative_to(ruta).as_posix()}:{_sha_fichero(p)}"
                    for p in ruta.rglob("*") if p.is_file() and p.name != "grupo.json")
    return hashlib.sha256("\n".join(lineas).encode()).hexdigest()


def sha_conocido(sistema: str, nombre: str) -> str | None:
    ruta = config.ROOT / sistema / nombre
    if not ruta.exists():
        return None
    tamano, mtime = _firma(ruta)
    with _conectar() as con:
        fila = con.execute("SELECT * FROM hashes WHERE ruta = ?", (f"{sistema}/{nombre}",)).fetchone()
    if fila and fila["tamano"] == tamano and abs(fila["mtime"] - mtime) < 1:
        return fila["sha"]
    return None


def _calcular_hashes_pendientes() -> None:
    vistos = set()
    for carpeta_sistema in sorted(p for p in config.ROOT.iterdir() if p.is_dir()):
        for juego in sorted(carpeta_sistema.iterdir()):
            if juego.name.startswith("."):
                continue
            clave = f"{carpeta_sistema.name}/{juego.name}"
            vistos.add(clave)
            if sha_conocido(carpeta_sistema.name, juego.name):
                continue
            try:
                tamano, mtime = _firma(juego)
                sha = _sha_juego(juego)
                with _conectar() as con:
                    con.execute("INSERT OR REPLACE INTO hashes (ruta, tamano, mtime, sha) VALUES (?, ?, ?, ?)",
                                (clave, tamano, mtime, sha))
            except OSError:
                continue  # se está subiendo o borrando justo ahora; en la próxima vuelta
    with _conectar() as con:
        for fila in con.execute("SELECT ruta FROM hashes").fetchall():
            if fila["ruta"] not in vistos:
                con.execute("DELETE FROM hashes WHERE ruta = ?", (fila["ruta"],))


def arrancar_hilo_hashes() -> None:
    def bucle():
        while True:
            try:
                _calcular_hashes_pendientes()
            except Exception as e:  # noqa: BLE001 -- el hilo no puede morir
                print(f"[federacion] error calculando hashes: {e}", flush=True)
            time.sleep(300)
    threading.Thread(target=bucle, name="hashes", daemon=True).start()


# =====================================================================
# Lado ÍNDICE (solo si ROMSLIBRARY_ES_INDICE)
# =====================================================================

def _exigir_indice() -> None:
    if not config.ES_INDICE:
        raise HTTPException(status_code=404, detail="Este servicio no es un índice de librerías.")


def _servicio_indice(con: sqlite3.Connection, ident: str, secreto: str) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM indice_servicios WHERE id = ?", (ident or "",)).fetchone()
    if fila is None or not secrets.compare_digest(fila["secreto_hash"], _hash_secreto(secreto or "")):
        raise HTTPException(status_code=403, detail="Librería no reconocida por el índice.")
    con.execute("UPDATE indice_servicios SET ultimo_contacto = ? WHERE id = ?", (time.time(), fila["id"]))
    return fila


def _alta_en_indice(url: str, nombre: str) -> dict:
    """Registra (o vuelve a registrar, con secreto nuevo) una URL ya verificada."""
    secreto = secrets.token_urlsafe(32)
    ahora = time.time()
    with _conectar() as con:
        fila = con.execute("SELECT id FROM indice_servicios WHERE url = ?", (url,)).fetchone()
        ident = fila["id"] if fila else secrets.token_hex(8)
        con.execute(
            "INSERT INTO indice_servicios (id, url, nombre, secreto_hash, publico, alta, ultimo_contacto) "
            "VALUES (?, ?, ?, ?, 1, ?, ?) ON CONFLICT(url) DO UPDATE SET nombre = excluded.nombre, "
            "secreto_hash = excluded.secreto_hash, publico = 1, ultimo_contacto = excluded.ultimo_contacto",
            (ident, url, nombre[:60], _hash_secreto(secreto), ahora, ahora))
    return {"id": ident, "secreto": secreto}


def _url_publica_valida(url: str) -> bool:
    """El índice hace una petición a la URL que le dan: solo a direcciones
    PÚBLICAS por https (nunca loopback, red privada, Tailscale/CGNAT,
    link-local...). Sin esto se le podía hacer pedir URLs internas (SSRF)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse
    partes = urlparse(url)
    if partes.scheme != "https" or not partes.hostname or partes.username or partes.password:
        return False
    try:
        direcciones = {info[4][0] for info in socket.getaddrinfo(partes.hostname, partes.port or 443)}
    except socket.gaierror:
        return False
    for d in direcciones:
        ip = ipaddress.ip_address(d)
        if not ip.is_global or ip in ipaddress.ip_network("100.64.0.0/10"):
            return False
    return True


@router.post("/indice/alta")
async def indice_alta(request: Request):
    _exigir_indice()
    datos = await request.json()
    url = str(datos.get("url", "")).rstrip("/")
    nombre = str(datos.get("nombre", "")).strip() or url
    codigo = str(datos.get("codigo", ""))
    if not url.startswith(("https://", "http://")) or not codigo:
        raise HTTPException(status_code=400, detail="Falta la URL pública o el código de verificación.")
    if not config.FEDERACION_URLS_PRIVADAS and not _url_publica_valida(url):
        raise HTTPException(status_code=400, detail="La URL pública tiene que ser https y resolver a una IP pública.")
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as cliente:
            r = await cliente.get(f"{url}/federacion/ping")
        devuelto = r.json().get("alta_pendiente") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        devuelto = None
    if not devuelto or not secrets.compare_digest(str(devuelto), codigo):
        raise HTTPException(status_code=403, detail=f"No se pudo verificar que {url} es tuya "
                                                    "(¿la URL pública es correcta y accesible desde internet?).")
    return _alta_en_indice(url, nombre)


@router.post("/indice/baja")
async def indice_baja(request: Request):
    _exigir_indice()
    datos = await request.json()
    with _conectar() as con:
        fila = _servicio_indice(con, datos.get("id"), datos.get("secreto"))
        con.execute("UPDATE indice_servicios SET publico = 0 WHERE id = ?", (fila["id"],))
    return {"ok": True}


@router.post("/indice/servicios")
async def indice_servicios(request: Request):
    _exigir_indice()
    datos = await request.json()
    with _conectar() as con:
        yo = _servicio_indice(con, datos.get("id"), datos.get("secreto"))
        if not yo["publico"]:
            raise HTTPException(status_code=403, detail="Solo las librerías públicas ven a las demás.")
        filas = con.execute("SELECT id, nombre, url FROM indice_servicios WHERE publico = 1 AND id != ? "
                            "ORDER BY nombre", (yo["id"],)).fetchall()
    return [dict(f) for f in filas]


@router.post("/indice/ticket")
async def indice_ticket(request: Request):
    _exigir_indice()
    datos = await request.json()
    with _conectar() as con:
        yo = _servicio_indice(con, datos.get("id"), datos.get("secreto"))
        destino = con.execute("SELECT * FROM indice_servicios WHERE id = ? AND publico = 1",
                              (datos.get("destino", ""),)).fetchone()
        if not yo["publico"] or destino is None:
            raise HTTPException(status_code=403, detail="Las dos librerías tienen que ser públicas.")
        ticket = secrets.token_urlsafe(32)
        con.execute("DELETE FROM indice_tickets WHERE caduca < ?", (time.time(),))
        con.execute("INSERT INTO indice_tickets (ticket, origen_id, destino_id, caduca) VALUES (?, ?, ?, ?)",
                    (ticket, yo["id"], destino["id"], time.time() + DURACION_TICKET))
    return {"ticket": ticket, "caduca": time.time() + DURACION_TICKET}


@router.post("/indice/verificar")
async def indice_verificar(request: Request):
    _exigir_indice()
    datos = await request.json()
    with _conectar() as con:
        yo = _servicio_indice(con, datos.get("id"), datos.get("secreto"))
        fila = con.execute(
            "SELECT t.*, s.nombre FROM indice_tickets t JOIN indice_servicios s ON s.id = t.origen_id "
            "WHERE t.ticket = ? AND t.destino_id = ? AND t.caduca > ? AND s.publico = 1",
            (datos.get("ticket", ""), yo["id"], time.time())).fetchone()
    if fila is None:
        return {"valido": False}
    return {"valido": True, "origen": {"id": fila["origen_id"], "nombre": fila["nombre"]}, "caduca": fila["caduca"]}


# =====================================================================
# Lado CLIENTE: alta/baja de este servicio en el índice
# =====================================================================

_alta_pendiente: dict = {}


@router.get("/ping")
async def ping():
    """El índice lo consulta para verificar el alta (ver cabecera)."""
    codigo = _alta_pendiente.get("codigo") if _alta_pendiente.get("caduca", 0) > time.time() else None
    return {"servicio": "RomsLibrary", "nombre": ajustes.leer("nombre"), "alta_pendiente": codigo}


async def _post_indice(ruta: str, datos: dict) -> dict:
    if config.ES_INDICE:
        raise RuntimeError("uso interno")  # el índice no se habla a sí mismo por HTTP (ver más abajo)
    async with httpx.AsyncClient(timeout=TIMEOUT_REMOTO) as cliente:
        r = await cliente.post(f"{config.INDICE_URL}/federacion/indice/{ruta}", json=datos)
    if r.status_code != 200:
        try:
            detalle = r.json().get("detail", f"HTTP {r.status_code}")
        except ValueError:
            detalle = f"HTTP {r.status_code}"
        raise HTTPException(status_code=502, detail=f"Índice: {detalle}")
    return r.json()


async def darse_de_alta() -> dict:
    """Se llama al arrancar, periódicamente y al marcar la librería como
    pública. Si ya tenía credenciales, no hace nada."""
    if not ajustes.publico():
        return {"ok": False, "motivo": "La librería es privada."}
    url = ajustes.leer("url_publica").rstrip("/")
    if not url:
        return {"ok": False, "motivo": "Falta la URL pública en los ajustes."}
    nombre = ajustes.leer("nombre")
    if config.ES_INDICE:
        # El propio índice se registra directamente en su tabla.
        with _conectar() as con:
            fila = con.execute("SELECT id FROM indice_servicios WHERE url = ?", (url,)).fetchone()
        if fila is None or not ajustes.leer("fed_secreto"):
            cred = _alta_en_indice(url, nombre)
            ajustes.guardar("fed_id", cred["id"])
            ajustes.guardar("fed_secreto", cred["secreto"])
        else:
            with _conectar() as con:
                con.execute("UPDATE indice_servicios SET publico = 1, nombre = ? WHERE id = ?", (nombre, fila["id"]))
        return {"ok": True}
    if ajustes.leer("fed_id") and ajustes.leer("fed_url") == url:
        try:
            await _post_indice("servicios", {"id": ajustes.leer("fed_id"), "secreto": ajustes.leer("fed_secreto")})
            return {"ok": True}
        except HTTPException:
            pass  # credenciales rechazadas o baja previa: se repite el alta
    codigo = secrets.token_urlsafe(24)
    _alta_pendiente.update(codigo=codigo, caduca=time.time() + 120)
    try:
        cred = await _post_indice("alta", {"url": url, "nombre": nombre, "codigo": codigo})
    finally:
        _alta_pendiente.clear()
    ajustes.guardar("fed_id", cred["id"])
    ajustes.guardar("fed_secreto", cred["secreto"])
    ajustes.guardar("fed_url", url)
    return {"ok": True}


async def darse_de_baja() -> None:
    if not ajustes.leer("fed_id"):
        return
    if config.ES_INDICE:
        with _conectar() as con:
            con.execute("UPDATE indice_servicios SET publico = 0 WHERE id = ?", (ajustes.leer("fed_id"),))
        return
    try:
        await _post_indice("baja", {"id": ajustes.leer("fed_id"), "secreto": ajustes.leer("fed_secreto")})
    except HTTPException:
        pass


def _cred() -> dict:
    return {"id": ajustes.leer("fed_id"), "secreto": ajustes.leer("fed_secreto")}


async def _llamar_indice(ruta: str, datos: dict) -> dict | list:
    """Igual que _post_indice pero, si este servicio ES el índice, sin HTTP."""
    if not config.ES_INDICE:
        return await _post_indice(ruta, datos)
    funciones = {"servicios": indice_servicios, "ticket": indice_ticket, "verificar": indice_verificar}

    class _Falsa:
        async def json(self):
            return datos
    return await funciones[ruta](_Falsa())


# =====================================================================
# Lado CLIENTE: catálogos de las demás librerías y tickets
# =====================================================================

_cache_servicios: dict = {}
_cache_catalogos: dict = {}
_cache_tickets: dict = {}


async def otras_librerias() -> list[dict]:
    if not ajustes.publico() or not ajustes.leer("fed_id"):
        return []
    if _cache_servicios.get("caduca", 0) > time.time():
        return _cache_servicios["lista"]
    try:
        lista = await _llamar_indice("servicios", _cred())
    except Exception as e:  # noqa: BLE001 -- sin índice, se sigue con la librería personal
        print(f"[federacion] índice no disponible: {e}", flush=True)
        lista = _cache_servicios.get("lista", [])
    _cache_servicios.update(lista=lista, caduca=time.time() + CACHE_CATALOGOS)
    return lista


def _servicio_por_id(lista: list[dict], ident: str) -> dict:
    for s in lista:
        if s["id"] == ident:
            return s
    raise HTTPException(status_code=404, detail="Esa librería ya no está disponible.")


async def _ticket_para(destino_id: str) -> str:
    guardado = _cache_tickets.get(destino_id)
    if guardado and guardado["caduca"] > time.time() + 60:
        return guardado["ticket"]
    resp = await _llamar_indice("ticket", {**_cred(), "destino": destino_id})
    _cache_tickets[destino_id] = resp
    return resp["ticket"]


_SHA_VALIDO = set("0123456789abcdef")


def _juego_remoto_valido(j) -> dict | None:
    """Los catálogos de otras librerías son datos NO fiables: solo se
    aceptan juegos con forma correcta (sistema conocido, nombre de fichero
    seguro, tamaño numérico, SHA hexadecimal) y solo esos campos. Sin esto,
    una librería maliciosa podía colar HTML/JS en el gestor (p.ej. en el
    nombre del sistema) o rutas raras en las copias."""
    import main as nucleo
    if not isinstance(j, dict):
        return None
    sistema, nombre = j.get("system"), j.get("filename")
    if sistema not in nucleo.EXTENSIONES_POR_SISTEMA or not isinstance(nombre, str) or len(nombre) > 200:
        return None
    try:
        nucleo.nombre_seguro(nombre)
    except HTTPException:
        return None
    if any(c in nombre for c in "<>\"\x00"):
        return None
    tamano = j.get("sizeBytes")
    if not isinstance(tamano, int) or tamano < 0:
        return None
    sha = j.get("sha")
    if sha is not None and not (isinstance(sha, str) and len(sha) == 64 and set(sha) <= _SHA_VALIDO):
        sha = None
    portada = j.get("coverUrl")
    portada = portada if isinstance(portada, str) and portada.startswith("/api/cover/") and len(portada) < 300 else None
    roles = j.get("roles") if isinstance(j.get("roles"), dict) else None
    if roles:
        roles = {k: int(v) for k, v in roles.items() if k in ("base", "update", "dlc") and isinstance(v, int)}
    tipo = j.get("tipoGrupo") if j.get("tipoGrupo") in (None, "switch_base_dlc") else None
    return {"system": sistema, "filename": nombre, "sizeBytes": tamano, "coverUrl": portada,
            "esGrupo": bool(j.get("esGrupo")), "tipoGrupo": tipo, "roles": roles, "sha": sha}


async def catalogo_de(servicio: dict) -> list[dict]:
    cache = _cache_catalogos.get(servicio["id"])
    if cache and cache["caduca"] > time.time():
        return cache["juegos"]
    try:
        ticket = await _ticket_para(servicio["id"])
        async with httpx.AsyncClient(timeout=TIMEOUT_REMOTO) as cliente:
            r = await cliente.get(f"{servicio['url']}/federacion/catalogo",
                                  headers={"X-RomsLibrary-Ticket": ticket})
        bruto = r.json() if r.status_code == 200 else []
        juegos = [v for v in (_juego_remoto_valido(j) for j in (bruto if isinstance(bruto, list) else [])) if v]
    except Exception as e:  # noqa: BLE001 -- una librería caída no rompe el listado
        print(f"[federacion] catálogo de {servicio['url']} no disponible: {e}", flush=True)
        juegos = cache["juegos"] if cache else []
    _cache_catalogos[servicio["id"]] = {"juegos": juegos, "caduca": time.time() + CACHE_CATALOGOS}
    return juegos


def juegos_propios(sistema: str | None = None, texto: str | None = None) -> list[dict]:
    import main as nucleo
    juegos = nucleo.listar_juegos_galeria(nucleo.PETICION_INTERNA, nucleo.TOKEN_SISTEMA, system=sistema, q=texto)
    for j in juegos:
        j["sha"] = sha_conocido(j["system"], j["filename"])
        j["origen"] = None
        j["libreria"] = ajustes.leer("nombre")
        j["editable"] = True
    return juegos


async def juegos_listado(sistema: str | None, texto: str | None, libreria: str) -> list[dict]:
    """libreria: "personal" | "otras" | "todas". Sin duplicados por SHA."""
    propios = juegos_propios(sistema, texto) if libreria in ("personal", "todas") else []
    if libreria == "personal":
        return propios
    vistos = {j["sha"] for j in juegos_propios() if j["sha"]}  # nunca repetir uno que ya tengo
    otros = []
    servicios = await otras_librerias()
    catalogos = await asyncio.gather(*(catalogo_de(s) for s in servicios))
    for servicio, catalogo in zip(servicios, catalogos):
        for j in catalogo:
            if sistema and j.get("system") != sistema:
                continue
            if texto and texto.lower() not in j.get("filename", "").lower():
                continue
            sha = j.get("sha")
            if sha and sha in vistos:
                continue
            if sha:
                vistos.add(sha)
            otros.append({**j, "origen": servicio["id"], "libreria": servicio["nombre"], "editable": False})
    return propios + otros


async def listar_para_app(sistema: str | None, texto: str | None, libreria: str) -> list[dict]:
    if libreria not in ("personal", "otras", "todas"):
        libreria = "personal"
    return await juegos_listado(sistema, texto, libreria)


# ---------- Peticiones a otra librería (la app y el gestor pasan por aquí) ----------

async def _url_remota(origen: str) -> tuple[dict, str]:
    servicio = _servicio_por_id(await otras_librerias(), origen)
    return servicio, await _ticket_para(servicio["id"])


async def json_remoto(origen: str, accion: str, params: dict):
    servicio, ticket = await _url_remota(origen)
    async with httpx.AsyncClient(timeout=TIMEOUT_REMOTO) as cliente:
        r = await cliente.get(f"{servicio['url']}/federacion/juego", params={"accion": accion, **params},
                              headers={"X-RomsLibrary-Ticket": ticket})
    return JSONResponse(r.json(), status_code=r.status_code)


async def caratula_remota(origen: str, sistema: str, nombre: str):
    servicio, ticket = await _url_remota(origen)
    async with httpx.AsyncClient(timeout=TIMEOUT_REMOTO) as cliente:
        r = await cliente.get(f"{servicio['url']}/federacion/juego",
                              params={"accion": "caratula", "system": sistema, "filename": nombre},
                              headers={"X-RomsLibrary-Ticket": ticket})
    if r.status_code != 200:
        raise HTTPException(status_code=404, detail="Carátula no encontrada.")
    return Response(r.content, media_type=r.headers.get("content-type", "image/png"),
                    headers={"Cache-Control": "private, max-age=3600"})


async def descarga_remota(request: Request, origen: str, accion: str, params: dict):
    """Reenvía en streaming (con Range, para reanudar) un fichero de otra librería."""
    servicio, ticket = await _url_remota(origen)
    cabeceras = {"X-RomsLibrary-Ticket": ticket}
    if request.headers.get("range"):
        cabeceras["Range"] = request.headers["range"]
    cliente = httpx.AsyncClient(timeout=httpx.Timeout(15, read=300))
    respuesta = await cliente.send(cliente.build_request(
        "GET", f"{servicio['url']}/federacion/juego", params={"accion": accion, **params}, headers=cabeceras),
        stream=True)

    async def trozos():
        try:
            async for trozo in respuesta.aiter_bytes(262144):
                yield trozo
        finally:
            await respuesta.aclose()
            await cliente.aclose()

    reenviar = {k: v for k, v in respuesta.headers.items()
                if k.lower() in ("content-length", "content-range", "accept-ranges", "content-disposition")}
    return StreamingResponse(trozos(), status_code=respuesta.status_code, headers=reenviar,
                             media_type=respuesta.headers.get("content-type", "application/octet-stream"))


# =====================================================================
# Lado SERVIDOR: lo que esta librería ofrece a las demás (con ticket)
# =====================================================================

_tickets_validados: dict = {}


async def _exigir_ticket(request: Request) -> dict:
    if not ajustes.publico():
        raise HTTPException(status_code=403, detail="Esta librería es privada.")
    ticket = request.headers.get("x-romslibrary-ticket", "")
    if not ticket:
        raise HTTPException(status_code=401, detail="Falta el ticket de federación.")
    validado = _tickets_validados.get(ticket)
    if validado and validado["caduca"] > time.time():
        return validado["origen"]
    resp = await _llamar_indice("verificar", {**_cred(), "ticket": ticket})
    if not resp.get("valido"):
        raise HTTPException(status_code=403, detail="Ticket de federación no válido.")
    _tickets_validados[ticket] = {"origen": resp["origen"], "caduca": resp["caduca"]}
    return resp["origen"]


@router.get("/catalogo")
async def catalogo(request: Request):
    await _exigir_ticket(request)
    return [{k: j[k] for k in ("system", "filename", "sizeBytes", "coverUrl", "esGrupo", "tipoGrupo", "roles", "sha")}
            for j in juegos_propios()]


@router.get("/juego")
async def juego(request: Request):
    """Lo mismo que la API de la app para un juego propio (descargar,
    ficheros de grupo, carátula), pero autenticado con ticket."""
    await _exigir_ticket(request)
    import main as nucleo
    q = request.query_params
    I, T = nucleo.PETICION_INTERNA, nucleo.TOKEN_SISTEMA
    accion = q.get("accion")
    if accion == "descargar":
        return nucleo.descargar_juego(q["system"], q["filename"], I, T)
    if accion == "grupo_ficheros":
        return nucleo.ficheros_de_grupo(I, T, system=q["system"], grupo=q["grupo"])
    if accion == "grupo_fichero":
        return nucleo.fichero_de_grupo(I, T, system=q["system"], grupo=q["grupo"], fichero=q["fichero"])
    if accion == "caratula":
        return nucleo.servir_caratula(q["system"], q["filename"], I, T)
    raise HTTPException(status_code=404, detail="Acción desconocida.")


# =====================================================================
# Copiar un juego de otra librería a la personal (en segundo plano)
# =====================================================================

def copias_en_curso() -> list[dict]:
    with _conectar() as con:
        return [dict(f) for f in con.execute("SELECT * FROM copias ORDER BY creada DESC LIMIT 20").fetchall()]


async def iniciar_copia(origen: str, sistema: str, fichero: str, nuevo_nombre: str) -> dict:
    import main as nucleo
    nucleo.nombre_seguro(sistema)
    nuevo_nombre = nucleo.nombre_seguro(nuevo_nombre.strip())
    carpeta_sistema = config.ROOT / sistema
    if nucleo.existe_juego(carpeta_sistema, Path(nuevo_nombre).stem) or (carpeta_sistema / nuevo_nombre).exists():
        raise HTTPException(status_code=409, detail=f"Ya tienes un juego llamado «{nuevo_nombre}».")
    servicio = _servicio_por_id(await otras_librerias(), origen)
    juego_remoto = next((j for j in await catalogo_de(servicio)
                         if j.get("system") == sistema and j.get("filename") == fichero), None)
    if juego_remoto is None:
        raise HTTPException(status_code=404, detail="Ese juego ya no está en la otra librería.")
    if juego_remoto.get("sha") and juego_remoto["sha"] in {j["sha"] for j in juegos_propios() if j["sha"]}:
        raise HTTPException(status_code=409, detail="Ese juego ya está en tu librería.")
    libre = shutil.disk_usage(config.ROOT).free
    if int(juego_remoto.get("sizeBytes") or 0) > libre - 2 * 1024**3:
        raise HTTPException(status_code=507, detail="No hay espacio libre suficiente para copiar ese juego.")
    with _conectar() as con:
        en_curso = con.execute("SELECT 1 FROM copias WHERE origen = ? AND sistema = ? AND fichero = ? "
                               "AND estado IN ('pendiente', 'copiando')", (origen, sistema, fichero)).fetchone()
    if en_curso:
        raise HTTPException(status_code=409, detail="Ese juego ya se está copiando.")
    with _conectar() as con:
        cur = con.execute("INSERT INTO copias (origen, sistema, fichero, nuevo_nombre, estado, bytes_total, creada) "
                          "VALUES (?, ?, ?, ?, 'pendiente', ?, ?)",
                          (origen, sistema, fichero, nuevo_nombre, int(juego_remoto.get("sizeBytes") or 0), time.time()))
        copia_id = cur.lastrowid
    asyncio.create_task(_copiar(copia_id, servicio, sistema, juego_remoto, nuevo_nombre))
    return {"ok": True, "copia": copia_id}


def _estado_copia(copia_id: int, **campos) -> None:
    with _conectar() as con:
        sets = ", ".join(f"{k} = ?" for k in campos)
        con.execute(f"UPDATE copias SET {sets} WHERE id = ?", (*campos.values(), copia_id))


async def _copiar(copia_id: int, servicio: dict, sistema: str, juego_remoto: dict, nuevo_nombre: str) -> None:
    """Se baja todo a una carpeta temporal y solo al final se mueve a la
    librería: nunca queda un juego a medias visible."""
    import main as nucleo
    # La otra librería declara el tamaño del juego: si manda más (un 5% de
    # margen), se corta. Sin tope, una librería maliciosa podía llenar el disco.
    limite = int(juego_remoto.get("sizeBytes") or 0) * 1.05 + 1024 * 1024
    temporal = config.TMP / f"copia_{copia_id}"
    shutil.rmtree(temporal, ignore_errors=True)
    temporal.mkdir(parents=True)
    hechos = 0
    try:
        _estado_copia(copia_id, estado="copiando")
        ticket = await _ticket_para(servicio["id"])
        cabecera = {"X-RomsLibrary-Ticket": ticket}
        url = f"{servicio['url']}/federacion/juego"
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, read=300)) as cliente:
            if juego_remoto.get("esGrupo"):
                r = await cliente.get(url, params={"accion": "grupo_ficheros", "system": sistema,
                                                   "grupo": juego_remoto["filename"]}, headers=cabecera)
                r.raise_for_status()
                ficheros = r.json()
                carpeta = temporal / "juego"
                for f in ficheros:
                    destino = carpeta / f["nombre"]
                    if not str(destino.resolve()).startswith(str(carpeta.resolve())):
                        raise RuntimeError("Ruta insegura en el grupo remoto")
                    destino.parent.mkdir(parents=True, exist_ok=True)
                    async with cliente.stream("GET", url, params={
                            "accion": "grupo_fichero", "system": sistema, "grupo": juego_remoto["filename"],
                            "fichero": f["nombre"]}, headers=cabecera) as resp:
                        resp.raise_for_status()
                        with open(destino, "wb") as salida:
                            async for trozo in resp.aiter_bytes(1024 * 1024):
                                salida.write(trozo)
                                hechos += len(trozo)
                                if hechos > limite:
                                    raise RuntimeError("La otra librería envía más datos de los que declara")
                                if hechos % (32 * 1024 * 1024) < len(trozo):
                                    _estado_copia(copia_id, bytes_hechos=hechos)
                    if f.get("rol"):
                        nucleo._registrar_rol_grupo(carpeta, f["nombre"], f["rol"])
                resultado = carpeta
            else:
                resultado = temporal / "juego.bin"
                async with cliente.stream("GET", url, params={"accion": "descargar", "system": sistema,
                                                              "filename": juego_remoto["filename"]},
                                          headers=cabecera) as resp:
                    resp.raise_for_status()
                    with open(resultado, "wb") as salida:
                        async for trozo in resp.aiter_bytes(1024 * 1024):
                            salida.write(trozo)
                            hechos += len(trozo)
                            if hechos > limite:
                                raise RuntimeError("La otra librería envía más datos de los que declara")
                            if hechos % (32 * 1024 * 1024) < len(trozo):
                                _estado_copia(copia_id, bytes_hechos=hechos)
            caratula = None
            if juego_remoto.get("coverUrl"):
                r = await cliente.get(url, params={"accion": "caratula", "system": sistema,
                                                   "filename": juego_remoto["coverUrl"].rsplit("/", 1)[-1]},
                                      headers=cabecera)
                caratula = r.content if r.status_code == 200 else None
        destino = _instalar_copia(sistema, resultado, juego_remoto, nuevo_nombre, caratula)
        if juego_remoto.get("sha"):
            # Mismo contenido que el original: su SHA vale ya, sin esperar
            # a la siguiente vuelta del cálculo en segundo plano (si no, se
            # podía copiar el mismo juego dos veces seguidas).
            tamano, mtime = _firma(destino)
            with _conectar() as con:
                con.execute("INSERT OR REPLACE INTO hashes (ruta, tamano, mtime, sha) VALUES (?, ?, ?, ?)",
                            (f"{sistema}/{destino.name}", tamano, mtime, juego_remoto["sha"]))
        _estado_copia(copia_id, estado="hecha", bytes_hechos=hechos)
    except Exception as e:  # noqa: BLE001 -- el error queda visible en el gestor
        _estado_copia(copia_id, estado="error", detalle=str(e)[:300])
    finally:
        shutil.rmtree(temporal, ignore_errors=True)


def _instalar_copia(sistema: str, resultado: Path, juego_remoto: dict, nuevo_nombre: str, caratula: bytes | None):
    import main as nucleo
    carpeta_sistema = config.ROOT / sistema
    carpeta_sistema.mkdir(parents=True, exist_ok=True)
    if juego_remoto.get("esGrupo"):
        # Los grupos se llaman "<juego>.cue"/".m3u"/".nsp" (un solo juego en
        # ES-DE): se reconstruye ese formato con el nombre nuevo.
        stem = Path(nuevo_nombre).stem if Path(nuevo_nombre).suffix.lower() in nucleo.EXTENSIONES_LANZABLES else nuevo_nombre
        viejo = Path(juego_remoto["filename"])
        stem_viejo = viejo.stem if viejo.suffix.lower() in nucleo.EXTENSIONES_LANZABLES else viejo.name
        for f in list(resultado.iterdir()):
            # El lanzable y su .sbi (los cores buscan el .sbi con el mismo nombre que el .cue).
            if f.stem == stem_viejo and f.suffix.lower() in nucleo.EXTENSIONES_LANZABLES | {".sbi"}:
                f.rename(resultado / (stem + f.suffix))
        ruta_json = resultado / "grupo.json"
        if ruta_json.is_file():
            import json
            datos = json.loads(ruta_json.read_text(encoding="utf-8"))
            for f in datos.get("ficheros", []):
                if Path(f["nombre"]).stem == stem_viejo:
                    f["nombre"] = stem + Path(f["nombre"]).suffix
            ruta_json.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        destino = carpeta_sistema / stem
        if destino.exists():
            raise RuntimeError(f"Ya existe «{stem}»")
        shutil.move(str(resultado), str(destino))
        destino = nucleo.carpeta_como_fichero(destino)
    else:
        extension = Path(juego_remoto["filename"]).suffix
        if not nuevo_nombre.lower().endswith(extension.lower()):
            nuevo_nombre += extension
        destino = carpeta_sistema / nuevo_nombre
        if destino.exists():
            raise RuntimeError(f"Ya existe «{nuevo_nombre}»")
        shutil.move(str(resultado), str(destino))
    if caratula:
        try:
            nucleo.guardar_caratula(caratula, config.COVERS_ROOT / sistema, Path(destino.name).stem)
        except ValueError:
            pass
    return destino
