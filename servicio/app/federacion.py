"""
Federación de librerías públicas de RomsLibrary.

Cada servicio es PRIVADO (por defecto: solo los suyos) o PÚBLICO (comparte
sus juegos y ve los de las demás librerías públicas). La lista de librerías
públicas la lleva el ÍNDICE, un servicio aparte (indice.py; el oficial está
en sund.es) al que cada librería pública se da de alta como una más.

Alta en el índice (prueba de que controlas la URL que declaras): el servicio
genera un código de un solo uso, lo guarda como "alta pendiente" y se lo
manda al índice junto a su URL; el índice pide GET <url>/federacion/ping y
solo da de alta si esa URL le devuelve el mismo código. Así nadie puede
registrar la URL de otro. El índice devuelve un id + secreto.

Entre librerías nunca viaja ese secreto: para leer el catálogo o los
ficheros de otra librería se pide al índice un TICKET (10 min, solo para esa
librería de destino) y se manda en X-RomsLibrary-Ticket; la librería que lo
recibe lo valida contra el índice (y lo cachea mientras dure).

Sin duplicados: cada juego tiene un SHA-256 (fichero suelto) o el SHA-256
de la lista ordenada "ruta:sha256" de sus ficheros (juegos de varios
ficheros). Si dos librerías tienen el mismo juego se muestra una sola vez,
con preferencia por el de la librería personal.

Los juegos de otras librerías nunca se pueden editar desde esta: solo
descargarlos o COPIARLOS a la librería personal (se bajan los ficheros y
pasan a ser propios). Las descargas van DIRECTAS de la librería de origen a
la app (por su API pública): este servicio nunca reenvía juegos ajenos.
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
from fastapi.responses import RedirectResponse, Response

import ajustes
import config
import externos
import linaje
import verificacion
import versiones

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
            CREATE TABLE IF NOT EXISTS copias (
                id INTEGER PRIMARY KEY, origen TEXT NOT NULL, sistema TEXT NOT NULL, fichero TEXT NOT NULL,
                nuevo_nombre TEXT NOT NULL, estado TEXT NOT NULL, detalle TEXT NOT NULL DEFAULT '',
                bytes_hechos INTEGER NOT NULL DEFAULT 0, bytes_total INTEGER NOT NULL DEFAULT 0,
                creada REAL NOT NULL
            );
            """
        )
        # El índice era antes parte de este servicio (ahora es indice.py, aparte).
        con.execute("DROP TABLE IF EXISTS indice_servicios")
        con.execute("DROP TABLE IF EXISTS indice_tickets")
        # Copias que estaban a medias cuando se paró el servicio.
        con.execute("UPDATE copias SET estado = 'error', detalle = 'Interrumpida al reiniciar el servicio' "
                    "WHERE estado IN ('pendiente', 'copiando')")


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
            sha = sha_conocido(carpeta_sistema.name, juego.name)
            try:
                tamano, mtime = _firma(juego)
                if not sha:
                    sha = _sha_juego(juego)
                    with _conectar() as con:
                        con.execute("INSERT OR REPLACE INTO hashes (ruta, tamano, mtime, sha) VALUES (?, ?, ?, ?)",
                                    (clave, tamano, mtime, sha))
                versiones.registrar(carpeta_sistema.name, juego.name, sha, tamano)
            except OSError:
                continue  # se está subiendo o borrando justo ahora; en la próxima vuelta
    with _conectar() as con:
        for fila in con.execute("SELECT ruta FROM hashes").fetchall():
            if fila["ruta"] not in vistos:
                con.execute("DELETE FROM hashes WHERE ruta = ?", (fila["ruta"],))
                versiones.olvidar_ruta(fila["ruta"])


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
# Lado CLIENTE: alta/baja de este servicio en el índice
# =====================================================================

_alta_pendiente: dict = {}


@router.get("/ping")
async def ping():
    """El índice lo consulta para verificar el alta (ver cabecera)."""
    codigo = _alta_pendiente.get("codigo") if _alta_pendiente.get("caduca", 0) > time.time() else None
    return {"servicio": "RomsLibrary", "nombre": ajustes.leer("nombre"), "alta_pendiente": codigo}


async def _post_indice(ruta: str, datos: dict) -> dict | list:
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
    try:
        await _post_indice("baja", {"id": ajustes.leer("fed_id"), "secreto": ajustes.leer("fed_secreto")})
    except HTTPException:
        pass


def _cred() -> dict:
    return {"id": ajustes.leer("fed_id"), "secreto": ajustes.leer("fed_secreto")}


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
        lista = await _post_indice("servicios", _cred())
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
    resp = await _post_indice("ticket", {**_cred(), "destino": destino_id})
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
    # Historial de versiones (para el linaje, ver linaje.py): [sha, tamaño] de la más vieja a la anterior a la actual.
    anteriores = []
    if sha and isinstance(j.get("anteriores"), list):
        for par in j["anteriores"][:100]:
            if (isinstance(par, list) and len(par) == 2 and isinstance(par[0], str) and len(par[0]) == 64
                    and set(par[0]) <= _SHA_VALIDO and isinstance(par[1], int) and par[1] >= 0 and par[0] != sha):
                anteriores.append([par[0], par[1]])
    codigo = j.get("codigo") if isinstance(j.get("codigo"), str) and len(j["codigo"]) <= 40 \
        and all(c.isalnum() or c in ":-_. " for c in j["codigo"]) else None
    return {"system": sistema, "filename": nombre, "sizeBytes": tamano, "coverUrl": portada,
            "esGrupo": bool(j.get("esGrupo")), "tipoGrupo": tipo, "roles": roles, "sha": sha,
            "version": len(anteriores) + 1 if sha else None, "anteriores": anteriores, "codigo": codigo,
            "fuenteExterna": _enlace_remoto(j.get("fuenteExterna")) if sha else None,
            "paginaAutor": _enlace_remoto(j.get("paginaAutor"))}


def _enlace_remoto(url) -> str | None:
    """Enlaces que publica otra librería: solo https, sin credenciales ni IPs
    privadas escritas a mano (la app además comprueba el SHA de lo que baja)."""
    import ipaddress
    from urllib.parse import urlparse
    if not isinstance(url, str) or len(url) > 500 or any(c in url for c in "<>\"\x00 "):
        return None
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        return None
    try:
        if not ipaddress.ip_address(p.hostname).is_global:
            return None
    except ValueError:
        if p.hostname == "localhost" or p.hostname.endswith((".local", ".internal", ".lan", ".ts.net")):
            return None
    return url


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
    cadenas = versiones.cadenas_propias()
    enlaces = externos.por_ruta()
    verificados = verificacion.todas()
    for j in juegos:
        j["sha"] = sha_conocido(j["system"], j["filename"])
        c = cadenas.get(f"{j['system']}/{j['filename']}")
        cadena = c["cadena"] if c and c["cadena"][-1][0] == j["sha"] else []
        j["version"] = len(cadena) or None
        j["anteriores"] = [[s, t] for s, t in cadena[:-1]]
        j["codigo"] = c["codigo"] if c else None
        e = enlaces.get(f"{j['system']}/{j['filename']}")
        # Descarga preferente desde la web del autor (ver externos.py).
        j["fuenteExterna"] = e["url"] if e else None
        j["paginaAutor"] = (e["pagina"] or None) if e else None
        j["fuenteEstado"] = e["estado"] if e else None   # solo para el gestor (no se exporta)
        v = verificados.get(j["sha"]) if j["sha"] else None
        # No-Intro / Redump (ver verificacion.py), solo para el gestor (no se exporta).
        j["verificacion"] = {k: v[k] for k in ("estado", "fuente", "nombre", "detalle")} if v else None
        j["origen"] = None
        j["libreria"] = ajustes.leer("nombre")
        j["editable"] = True
    return juegos


async def red() -> tuple[list[dict], list[dict], list[list[dict]], linaje.Linaje]:
    """Juegos propios, librerías públicas, sus catálogos y el linaje de
    versiones que sale de todo ello (ver linaje.py)."""
    propios = juegos_propios()
    servicios = await otras_librerias()
    catalogos = await asyncio.gather(*(catalogo_de(s) for s in servicios))
    lin = linaje.Linaje()
    for j in propios:
        if j["sha"]:
            lin.anadir([s for s, _ in j["anteriores"]] + [j["sha"]], "propia")
    fiables = sorted((x for x in zip(servicios, catalogos) if x[0].get("confianza", 0) >= linaje.UMBRAL),
                     key=lambda x: -x[0].get("confianza", 0))
    for servicio, catalogo in fiables:
        for j in catalogo:
            if j.get("sha"):
                lin.anadir([s for s, _ in j["anteriores"]] + [j["sha"]], servicio["id"])
    return propios, servicios, catalogos, lin


async def juegos_listado(sistema: str | None, texto: str | None, libreria: str) -> list[dict]:
    """libreria: "personal" | "otras" | "todas". Sin duplicados por SHA y,
    de otras librerías, sin versiones que se sabe que están superadas."""
    if libreria == "personal":
        return juegos_propios(sistema, texto)
    todos, servicios, catalogos, lin = await red()
    propios = juegos_propios(sistema, texto) if libreria == "todas" else []
    for j in propios:
        # Una librería de confianza tiene una versión más nueva de este juego.
        j["hayVersionNueva"] = bool(j["sha"] and lin.superada(j["sha"]))
    vistos = {j["sha"] for j in todos if j["sha"]}  # nunca repetir uno que ya tengo
    otros = []
    for servicio, catalogo in zip(servicios, catalogos):
        for j in catalogo:
            if sistema and j.get("system") != sistema:
                continue
            if texto and texto.lower() not in j.get("filename", "").lower():
                continue
            sha = j.get("sha")
            if sha and (sha in vistos or lin.superada(sha)):
                continue
            if sha:
                vistos.add(sha)
            otros.append({**j, "origen": servicio["id"], "origenUrl": servicio["url"],
                          "libreria": servicio["nombre"], "confianza": servicio.get("confianza", 0),
                          "version": lin.numero(sha) if sha else None, "editable": False})
    return propios + otros


async def obsoletas_para_app() -> list[dict]:
    """Versiones superadas (propias y de librerías de confianza) y dónde está
    la actual de cada una: la app actualiza con esto lo que tenga instalado."""
    propios, servicios, catalogos, lin = await red()
    donde: dict[str, dict] = {}       # sha actual -> dónde descargarlo
    tamanos: dict[str, int] = {}
    for j in propios:
        if j["sha"] and not j.get("esGrupo"):
            donde.setdefault(j["sha"], {"filename": j["filename"], "sizeBytes": j["sizeBytes"], "origenUrl": None,
                                        "fuenteExterna": j.get("fuenteExterna")})
        for s, t in j["anteriores"]:
            tamanos[s] = t
    for servicio, catalogo in zip(servicios, catalogos):
        for j in catalogo:
            if j.get("sha") and not j.get("esGrupo"):
                donde.setdefault(j["sha"], {"filename": j["filename"], "sizeBytes": j["sizeBytes"],
                                            "origenUrl": servicio["url"], "fuenteExterna": j.get("fuenteExterna")})
            for s, t in j.get("anteriores", []):
                tamanos.setdefault(s, t)
    sistema_de = {j["sha"]: j["system"] for j in propios if j["sha"]}
    for catalogo in catalogos:
        for j in catalogo:
            if j.get("sha"):
                sistema_de.setdefault(j["sha"], j["system"])
    res = []
    for viejo, n, actual, n_actual in lin.superadas():
        if actual not in donde or viejo not in tamanos and viejo not in {j["sha"] for j in propios}:
            continue
        tam = tamanos.get(viejo) or next((j["sizeBytes"] for j in propios if j["sha"] == viejo), None)
        if tam is None:
            continue
        d = donde[actual]
        res.append({"sistema": sistema_de.get(actual), "sha": viejo, "tamano": tam, "n": n,
                    "actual": {"filename": d["filename"], "sha": actual, "n": n_actual, "sizeBytes": d["sizeBytes"],
                               "origenUrl": d["origenUrl"], "fuenteExterna": d["fuenteExterna"]}})
    return res


async def listar_para_app(sistema: str | None, texto: str | None, libreria: str) -> list[dict]:
    if libreria not in ("personal", "otras", "todas"):
        libreria = "personal"
    return await juegos_listado(sistema, texto, libreria)


# ---------- Juegos de otra librería: siempre directos desde su origen ----------

RUTA_API_APP = "/APIs/RomLibraryApp"


def url_directa(servicio_url: str, params: dict) -> str:
    """URL de la API PÚBLICA de la otra librería (sin clave: es pública).
    La app y el navegador del gestor descargan de ahí directamente."""
    return f"{servicio_url}{RUTA_API_APP}?{httpx.QueryParams(params)}"


async def descarga_directa(origen: str, sistema: str, nombre: str) -> RedirectResponse:
    """Para el gestor: lo manda a la otra librería en vez de reenviar el fichero."""
    servicio = _servicio_por_id(await otras_librerias(), origen)
    return RedirectResponse(url_directa(servicio["url"], {"accion": "descargar", "system": sistema, "filename": nombre}),
                            status_code=302)


async def caratula_remota(origen: str, sistema: str, nombre: str):
    """Solo para el gestor (detrás de Authentik): la carátula pasa por aquí
    con el ticket, para no depender de la otra librería en el navegador."""
    servicio = _servicio_por_id(await otras_librerias(), origen)
    ticket = await _ticket_para(servicio["id"])
    async with httpx.AsyncClient(timeout=TIMEOUT_REMOTO) as cliente:
        r = await cliente.get(f"{servicio['url']}/federacion/juego",
                              params={"accion": "caratula", "system": sistema, "filename": nombre},
                              headers={"X-RomsLibrary-Ticket": ticket})
    if r.status_code != 200:
        raise HTTPException(status_code=404, detail="Carátula no encontrada.")
    return Response(r.content, media_type=r.headers.get("content-type", "image/png"),
                    headers={"Cache-Control": "private, max-age=3600"})


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
    resp = await _post_indice("verificar", {**_cred(), "ticket": ticket})
    if not resp.get("valido"):
        raise HTTPException(status_code=403, detail="Ticket de federación no válido.")
    _tickets_validados[ticket] = {"origen": resp["origen"], "caduca": resp["caduca"]}
    return resp["origen"]


@router.get("/catalogo")
async def catalogo(request: Request):
    await _exigir_ticket(request)
    return [{k: j[k] for k in ("system", "filename", "sizeBytes", "coverUrl", "esGrupo", "tipoGrupo", "roles", "sha",
                               "version", "anteriores", "codigo", "fuenteExterna", "paginaAutor")}
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
