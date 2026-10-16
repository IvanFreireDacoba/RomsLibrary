"""
Servicio de PARTIDAS de RomsLibrary: guardados y save states de las consolas,
APARTE de cualquier librería (en sund.es, su propio contenedor y su propia
base de datos). No depende de que ninguna librería sea pública o privada:
las apps guardan siempre aquí, venga el juego de la librería que venga, y
las librerías nunca ven partidas ni credenciales de consolas.

    uvicorn partidas:app        (mismo contenedor que el servicio, otro comando)

Identidad de cada consola:
- identificador "XXXXX-XXXXX": público, es lo que se comparte para vincular.
- secreto: el ANDROID_ID en Android, una credencial aleatoria en PC. Es la
  contraseña de la consola y solo viaja aquí (POST, por HTTPS). Se guarda
  como HMAC-SHA256 con una clave del servidor (PARTIDAS_PIMIENTA, fuera de
  la base de datos): sin ella, una copia de la base de datos no sirve para
  nada, y con ella la consola recupera su identidad al reinstalar la app.
- sesión: POST /sesion cambia el secreto por un token temporal (30 días,
  aquí solo su hash) que va en la cabecera Authorization.

Las partidas son del GRUPO de la consola (saves/<grupo>/<sistema>/<clave>/
<fichero>). Vincular dos consolas pide aceptar desde la otra: así nadie
mezcla sus partidas con las de una consola que no es suya. De cada fichero
se guardan las VERSIONES_GUARDADAS anteriores (saves/<grupo>/.versiones/),
para recuperar una partida sobrescrita:

    python partidas.py versiones <identificador de consola>
    python partidas.py restaurar <identificador> <sistema>/<clave>/<fichero> <versión>

Migración desde una librería (antes guardaba ella las partidas):

    python partidas.py migrar <romslibrary.db> <carpeta saves>
"""

import contextlib
import hashlib
import hmac
import os
import secrets
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

DATOS = Path(os.environ.get("ROMSLIBRARY_DATOS", "/datos"))
DB_PATH = DATOS / "partidas.db"
SAVES = DATOS / "saves"
PREFIJO = "/partidas/v1"

SESION_DIAS = 30
VERSIONES_GUARDADAS = 3
GUARDADO_MAX_BYTES = 512 * 1024 * 1024           # un fichero (memory card / NAND de Switch...)
CUOTA_GRUPO_BYTES = 4 * 1024 ** 3                # partidas actuales de un grupo de consolas
DISCO_LIBRE_MINIMO = 20 * 1024 ** 3
CONSOLAS_NUEVAS_POR_IP_DIA = 5
SESIONES_POR_IP_HORA = 60
SOLICITUDES_POR_CONSOLA_DIA = 20

_cerrojo_grupos = threading.Lock()


def _pimienta() -> bytes:
    """Clave del HMAC de los secretos. Va en el .env del contenedor; si no,
    se genera una vez en DATOS (peor: viaja con las copias de los datos)."""
    if os.environ.get("PARTIDAS_PIMIENTA"):
        return os.environ["PARTIDAS_PIMIENTA"].encode()
    fichero = DATOS / "pimienta"
    if not fichero.exists():
        fichero.write_text(secrets.token_hex(32))
        fichero.chmod(0o600)
    return fichero.read_text().strip().encode()


def huella(secreto: str) -> str:
    return hmac.new(_pimienta(), secreto.encode(), hashlib.sha256).hexdigest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def inicializar() -> None:
    DATOS.mkdir(parents=True, exist_ok=True)
    SAVES.mkdir(parents=True, exist_ok=True)
    with _conectar() as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS grupos (
                id INTEGER PRIMARY KEY, identificador TEXT NOT NULL UNIQUE, creado REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS consolas (
                id INTEGER PRIMARY KEY,
                huella TEXT NOT NULL UNIQUE,          -- HMAC del secreto (nunca el secreto)
                identificador TEXT NOT NULL UNIQUE,
                nombre TEXT NOT NULL,
                grupo_id INTEGER NOT NULL REFERENCES grupos(id),
                creada REAL NOT NULL, ultima_conexion REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sesiones (
                token TEXT PRIMARY KEY,               -- sha256 del token
                consola_id INTEGER NOT NULL REFERENCES consolas(id) ON DELETE CASCADE,
                caduca REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS solicitudes (
                id INTEGER PRIMARY KEY,
                origen_id INTEGER NOT NULL REFERENCES consolas(id) ON DELETE CASCADE,
                destino_id INTEGER NOT NULL REFERENCES consolas(id) ON DELETE CASCADE,
                creada REAL NOT NULL, UNIQUE (origen_id, destino_id)
            );
            CREATE TABLE IF NOT EXISTS bloqueos (
                bloqueador_id INTEGER NOT NULL REFERENCES consolas(id) ON DELETE CASCADE,
                bloqueado_id INTEGER NOT NULL REFERENCES consolas(id) ON DELETE CASCADE,
                creado REAL NOT NULL, PRIMARY KEY (bloqueador_id, bloqueado_id)
            );
            CREATE TABLE IF NOT EXISTS limites (
                clave TEXT NOT NULL, momento REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS limites_clave ON limites (clave, momento);
            """
        )
    _pimienta()


class Error(HTTPException):
    def __init__(self, mensaje: str, codigo: int = 400):
        super().__init__(status_code=codigo, detail=mensaje)


# ---------------- límites de uso (persisten entre reinicios) ----------------

def _limite(clave: str, maximo: int, ventana: float) -> None:
    ahora = time.time()
    with _conectar() as con:
        con.execute("DELETE FROM limites WHERE momento < ?", (ahora - 86400,))
        n = con.execute("SELECT COUNT(*) FROM limites WHERE clave = ? AND momento > ?",
                        (clave, ahora - ventana)).fetchone()[0]
        if n >= maximo:
            raise Error("Demasiadas peticiones: prueba más tarde.", 429)
        con.execute("INSERT INTO limites (clave, momento) VALUES (?, ?)", (clave, ahora))


def _ip(request: Request) -> str:
    # Solo se llega aquí por el nginx de Contabo (puerto en la IP de Tailscale).
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")


# ---------------- identificadores y grupos ----------------

def _nuevo_identificador(con, tabla: str) -> str:
    while True:
        n = 1 + secrets.randbelow(10 ** 10 - 1)
        ident = f"{n // 10 ** 5:05d}-{n % 10 ** 5:05d}"
        if not con.execute(f"SELECT 1 FROM {tabla} WHERE identificador = ?", (ident,)).fetchone():
            return ident


def _identificador_valido(ident: str) -> bool:
    return len(ident) == 11 and ident[5] == "-" and (ident[:5] + ident[6:]).isdigit() and ident != "00000-00000"


def _nuevo_grupo(con) -> sqlite3.Row:
    cur = con.execute("INSERT INTO grupos (identificador, creado) VALUES (?, ?)",
                      (_nuevo_identificador(con, "grupos"), time.time()))
    return con.execute("SELECT * FROM grupos WHERE id = ?", (cur.lastrowid,)).fetchone()


def _carpeta(con, consola) -> Path:
    grupo = con.execute("SELECT identificador FROM grupos WHERE id = ?", (consola["grupo_id"],)).fetchone()
    return SAVES / grupo["identificador"]


def _limpiar_nombre(nombre: str) -> str:
    nombre = " ".join(str(nombre or "").split())[:40]
    if not nombre:
        raise Error("Ponle un nombre a la consola.")
    return nombre


# ---------------- sesión ----------------

def _consola(request: Request) -> sqlite3.Row:
    cabecera = request.headers.get("authorization", "")
    if not cabecera.startswith("Bearer "):
        raise Error("Falta la sesión.", 401)
    with _conectar() as con:
        fila = con.execute("SELECT c.*, s.caduca FROM sesiones s JOIN consolas c ON c.id = s.consola_id "
                           "WHERE s.token = ?", (_hash_token(cabecera[7:].strip()),)).fetchone()
        if fila is None or fila["caduca"] < time.time():
            raise Error("La sesión ha caducado.", 401)
        con.execute("UPDATE consolas SET ultima_conexion = ? WHERE id = ?", (time.time(), fila["id"]))
        return fila


@contextlib.asynccontextmanager
async def _vida(_app):
    inicializar()
    yield


app = FastAPI(title="RomsLibrary · partidas", docs_url=None, redoc_url=None, openapi_url=None, lifespan=_vida)


@app.post(PREFIJO + "/sesion")
async def sesion(request: Request):
    """{secreto, nombre}: entra con la consola de ese secreto o, si no existe y
    viene nombre, la crea. Devuelve el token de sesión y la identidad."""
    datos = await request.json()
    secreto = str(datos.get("secreto") or "").strip()
    if not 8 <= len(secreto) <= 128:
        raise Error("Credencial de consola inválida.")
    ip = _ip(request)
    _limite("sesion:" + ip, SESIONES_POR_IP_HORA, 3600)
    h = huella(secreto)
    with _cerrojo_grupos, _conectar() as con:
        fila = con.execute("SELECT * FROM consolas WHERE huella = ?", (h,)).fetchone()
        if fila is None:
            if not datos.get("nombre"):
                raise Error("Esta consola aún no está registrada.", 404)
            nombre = _limpiar_nombre(datos["nombre"])
            _limite("nueva:" + ip, CONSOLAS_NUEVAS_POR_IP_DIA, 86400)
            grupo = _nuevo_grupo(con)
            ahora = time.time()
            con.execute("INSERT INTO consolas (huella, identificador, nombre, grupo_id, creada, ultima_conexion) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (h, _nuevo_identificador(con, "consolas"), nombre, grupo["id"],
                                                      ahora, ahora))
            fila = con.execute("SELECT * FROM consolas WHERE huella = ?", (h,)).fetchone()
        token = secrets.token_urlsafe(32)
        caduca = time.time() + SESION_DIAS * 86400
        con.execute("DELETE FROM sesiones WHERE caduca < ?", (time.time(),))
        con.execute("INSERT INTO sesiones (token, consola_id, caduca) VALUES (?, ?, ?)",
                    (_hash_token(token), fila["id"], caduca))
    return {"token": token, "caduca": caduca, "identificador": fila["identificador"], "nombre": fila["nombre"]}


# ---------------- consolas: estado y vinculación ----------------

def _resumen(f) -> dict:
    return {"nombre": f["nombre"], "identificador": f["identificador"]}


@app.get(PREFIJO + "/estado")
def estado(request: Request):
    yo = _consola(request)
    with _conectar() as con:
        sinc = con.execute("SELECT * FROM consolas WHERE grupo_id = ? AND id != ? ORDER BY nombre",
                           (yo["grupo_id"], yo["id"])).fetchall()
        recibidas = con.execute("SELECT s.id AS solicitud, c.* FROM solicitudes s JOIN consolas c ON c.id = s.origen_id "
                                "WHERE s.destino_id = ? ORDER BY s.creada", (yo["id"],)).fetchall()
        enviadas = con.execute("SELECT s.id AS solicitud, c.* FROM solicitudes s JOIN consolas c ON c.id = s.destino_id "
                               "WHERE s.origen_id = ? ORDER BY s.creada", (yo["id"],)).fetchall()
        bloqueadas = con.execute("SELECT c.* FROM bloqueos b JOIN consolas c ON c.id = b.bloqueado_id "
                                 "WHERE b.bloqueador_id = ? ORDER BY c.nombre", (yo["id"],)).fetchall()
    return {"yo": _resumen(yo), "sincronizadas": [_resumen(f) for f in sinc],
            "solicitudes_recibidas": [{"solicitud": f["solicitud"], **_resumen(f)} for f in recibidas],
            "solicitudes_enviadas": [{"solicitud": f["solicitud"], **_resumen(f)} for f in enviadas],
            "bloqueadas": [_resumen(f) for f in bloqueadas]}


def _otra(con, ident: str):
    ident = str(ident or "").strip()
    if not _identificador_valido(ident):
        raise Error("El identificador tiene que tener el formato 12345-67890.")
    return con.execute("SELECT * FROM consolas WHERE identificador = ?", (ident,)).fetchone()


def _bloqueado(con, quien: int, a_quien: int) -> bool:
    return con.execute("SELECT 1 FROM bloqueos WHERE bloqueador_id = ? AND bloqueado_id = ?",
                       (quien, a_quien)).fetchone() is not None


def _fusionar(origen: Path, destino: Path) -> None:
    """Mueve las partidas de origen a destino; si están en los dos, gana la
    más reciente y la otra queda como versión anterior (no se pierde)."""
    if not origen.is_dir():
        return
    destino.mkdir(parents=True, exist_ok=True)
    for f in sorted(origen.rglob("*")):
        if not f.is_file() or ".versiones" in f.relative_to(origen).parts:
            continue
        rel = f.relative_to(origen)
        objetivo = destino / rel
        if objetivo.exists():
            if objetivo.stat().st_mtime >= f.stat().st_mtime:
                _guardar_version(destino, rel, f)
                continue
            _guardar_version(destino, rel, objetivo)
        objetivo.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(objetivo))
    shutil.rmtree(origen, ignore_errors=True)


def _sacar_del_grupo(con, consola_id: int) -> None:
    """La consola pasa a un grupo propio con una COPIA de las partidas actuales."""
    fila = con.execute("SELECT * FROM consolas WHERE id = ?", (consola_id,)).fetchone()
    actual = _carpeta(con, fila)
    nuevo = _nuevo_grupo(con)
    if actual.is_dir():
        shutil.copytree(actual, SAVES / nuevo["identificador"], ignore=shutil.ignore_patterns(".versiones"))
    con.execute("UPDATE consolas SET grupo_id = ? WHERE id = ?", (nuevo["id"], consola_id))


@app.post(PREFIJO + "/consola/{accion}")
async def consola_accion(accion: str, request: Request):
    """renombrar {nombre} · solicitar / desincronizar / bloquear / desbloquear
    {otra} · responder {solicitud, aceptar} · cancelar {solicitud} · olvidar
    (borra esta consola y, si era la última de su grupo, sus partidas)."""
    yo = _consola(request)
    datos = await request.json()
    with _cerrojo_grupos, _conectar() as con:
        if accion == "renombrar":
            nombre = _limpiar_nombre(datos.get("nombre"))
            con.execute("UPDATE consolas SET nombre = ? WHERE id = ?", (nombre, yo["id"]))
            return {"identificador": yo["identificador"], "nombre": nombre}
        if accion == "solicitar":
            # Misma respuesta exista o no la otra consola: no sirve para ir
            # probando identificadores (y hay un tope diario por consola).
            _limite(f"solicitar:{yo['id']}", SOLICITUDES_POR_CONSOLA_DIA, 86400)
            otra = _otra(con, datos.get("otra"))
            if otra is not None and otra["id"] == yo["id"]:
                raise Error("Ese es el identificador de esta misma consola.")
            if otra is not None and otra["grupo_id"] == yo["grupo_id"]:
                raise Error("Esa consola ya está sincronizada con esta.")
            if otra is not None and _bloqueado(con, yo["id"], otra["id"]):
                raise Error("Tienes esa consola bloqueada: desbloquéala antes en «Consolas bloqueadas».")
            if otra is not None and not _bloqueado(con, otra["id"], yo["id"]):
                con.execute("INSERT OR IGNORE INTO solicitudes (origen_id, destino_id, creada) VALUES (?, ?, ?)",
                            (yo["id"], otra["id"], time.time()))
            return {"ok": True}
        if accion in ("responder", "cancelar"):
            solicitud = int(datos.get("solicitud") or 0)
            if accion == "cancelar":
                con.execute("DELETE FROM solicitudes WHERE id = ? AND origen_id = ?", (solicitud, yo["id"]))
                return {"ok": True}
            sol = con.execute("SELECT * FROM solicitudes WHERE id = ? AND destino_id = ?",
                              (solicitud, yo["id"])).fetchone()
            if sol is None:
                raise Error("Esa solicitud ya no existe.", 404)
            origen = con.execute("SELECT * FROM consolas WHERE id = ?", (sol["origen_id"],)).fetchone()
            con.execute("DELETE FROM solicitudes WHERE (origen_id = ? AND destino_id = ?) OR "
                        "(origen_id = ? AND destino_id = ?)", (origen["id"], yo["id"], yo["id"], origen["id"]))
            if not datos.get("aceptar"):
                return {"ok": True, "aceptada": False}
            # El grupo de quien acepta se une al de quien pidió.
            actual = con.execute("SELECT grupo_id FROM consolas WHERE id = ?", (yo["id"],)).fetchone()["grupo_id"]
            if actual != origen["grupo_id"]:
                viejo = con.execute("SELECT * FROM grupos WHERE id = ?", (actual,)).fetchone()
                nuevo = con.execute("SELECT * FROM grupos WHERE id = ?", (origen["grupo_id"],)).fetchone()
                _fusionar(SAVES / viejo["identificador"], SAVES / nuevo["identificador"])
                con.execute("UPDATE consolas SET grupo_id = ? WHERE grupo_id = ?", (nuevo["id"], viejo["id"]))
                con.execute("DELETE FROM grupos WHERE id = ?", (viejo["id"],))
            return {"ok": True, "aceptada": True, "con": _resumen(origen)}
        if accion == "olvidar":
            solos = con.execute("SELECT COUNT(*) FROM consolas WHERE grupo_id = ?", (yo["grupo_id"],)).fetchone()[0]
            carpeta = _carpeta(con, yo)
            con.execute("DELETE FROM consolas WHERE id = ?", (yo["id"],))
            if solos == 1:
                con.execute("DELETE FROM grupos WHERE id = ?", (yo["grupo_id"],))
                shutil.rmtree(carpeta, ignore_errors=True)
            return {"ok": True}
        if accion not in ("desincronizar", "bloquear", "desbloquear"):
            raise Error("Acción desconocida.", 404)
        otra = _otra(con, datos.get("otra"))
        if otra is None:
            _limite(f"buscar:{yo['id']}", 30, 86400)       # tampoco sirve para probar identificadores
            raise Error("No hay ninguna consola con ese identificador.", 404)
        actual = con.execute("SELECT grupo_id FROM consolas WHERE id = ?", (yo["id"],)).fetchone()["grupo_id"]
        if accion == "desincronizar":
            if otra["id"] == yo["id"] or otra["grupo_id"] != actual:
                raise Error("Esa consola no está sincronizada con esta.")
            _sacar_del_grupo(con, otra["id"])
            return {"ok": True}
        if accion == "bloquear":
            if otra["id"] == yo["id"]:
                raise Error("No puedes bloquear esta misma consola.")
            if otra["grupo_id"] == actual:
                _sacar_del_grupo(con, otra["id"])
            con.execute("DELETE FROM solicitudes WHERE (origen_id = ? AND destino_id = ?) OR "
                        "(origen_id = ? AND destino_id = ?)", (otra["id"], yo["id"], yo["id"], otra["id"]))
            con.execute("INSERT OR IGNORE INTO bloqueos (bloqueador_id, bloqueado_id, creado) VALUES (?, ?, ?)",
                        (yo["id"], otra["id"], time.time()))
            return {"ok": True}
        if accion == "desbloquear":
            con.execute("DELETE FROM bloqueos WHERE bloqueador_id = ? AND bloqueado_id = ?", (yo["id"], otra["id"]))
            return {"ok": True}
    raise Error("Acción desconocida.", 404)


# ---------------- partidas ----------------

def _segura(valor: str) -> str:
    valor = str(valor or "")
    if not valor or valor in (".", "..") or "/" in valor or "\\" in valor or "\x00" in valor \
            or valor.startswith(".") or len(valor) > 200:
        raise Error("Nombre de partida inválido.")
    return valor


def _ruta(carpeta: Path, sistema: str, clave: str, nombre: str) -> tuple[Path, Path]:
    rel = Path(_segura(sistema)) / _segura(clave) / _segura(nombre)
    return carpeta / rel, rel


def _guardar_version(carpeta: Path, rel: Path, fichero: Path) -> None:
    """Copia `fichero` como versión anterior de `rel` y deja solo las últimas."""
    destino = carpeta / ".versiones" / rel
    destino.mkdir(parents=True, exist_ok=True)
    marca = f"{fichero.stat().st_mtime:.3f}"
    shutil.copy2(fichero, destino / marca)
    for vieja in sorted(destino.iterdir(), key=lambda p: float(p.name))[:-VERSIONES_GUARDADAS]:
        vieja.unlink(missing_ok=True)


def _ocupado(carpeta: Path, excepto: Path) -> int:
    if not carpeta.is_dir():
        return 0
    return sum(f.stat().st_size for f in carpeta.rglob("*")
               if f.is_file() and f != excepto and ".versiones" not in f.relative_to(carpeta).parts)


@app.get(PREFIJO + "/indice")
def indice(request: Request):
    """Todas las partidas del grupo (sin versiones anteriores): la app compara
    en una sola petición qué subir y qué bajar."""
    yo = _consola(request)
    with _conectar() as con:
        carpeta = _carpeta(con, yo)
    if not carpeta.is_dir():
        return []
    res = []
    for f in carpeta.rglob("*"):
        partes = f.relative_to(carpeta).parts
        if f.is_file() and len(partes) == 3 and not partes[0].startswith(".") and not f.name.endswith(".subiendo"):
            st = f.stat()
            res.append({"sistema": partes[0], "clave": partes[1], "nombre_fichero": partes[2],
                        "mtime": st.st_mtime, "tamano": st.st_size})
    return res


@app.post(PREFIJO + "/partida")
async def subir(request: Request, sistema: str = Form(...), clave: str = Form(...), nombre_fichero: str = Form(...),
                mtime_dispositivo: float = Form(...), fichero: UploadFile = File(...)):
    yo = _consola(request)
    with _conectar() as con:
        carpeta = _carpeta(con, yo)
    ruta, rel = _ruta(carpeta, sistema, clave, nombre_fichero)
    if shutil.disk_usage(SAVES).free < DISCO_LIBRE_MINIMO:
        raise Error("El servidor no tiene espacio para más partidas.", 507)
    # Una fecha del futuro ganaría siempre: como mucho, la de ahora.
    mtime = min(mtime_dispositivo, time.time())
    disponible = min(GUARDADO_MAX_BYTES, CUOTA_GRUPO_BYTES - _ocupado(carpeta, ruta))
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(ruta.name + ".subiendo")
    escritos = 0
    try:
        with open(temporal, "wb") as salida:
            while trozo := await fichero.read(1024 * 1024):
                escritos += len(trozo)
                if escritos > disponible:
                    raise Error("Partida demasiado grande." if escritos > GUARDADO_MAX_BYTES
                                else "Se ha llenado el espacio de partidas de estas consolas.", 413)
                salida.write(trozo)
    except BaseException:
        temporal.unlink(missing_ok=True)
        raise
    os.utime(temporal, (mtime, mtime))
    if ruta.is_file():
        _guardar_version(carpeta, rel, ruta)
    temporal.replace(ruta)
    return {"ok": True, "mtime": mtime}


@app.get(PREFIJO + "/partida")
def bajar(request: Request, sistema: str = Query(...), clave: str = Query(...), nombre_fichero: str = Query(...)):
    yo = _consola(request)
    with _conectar() as con:
        carpeta = _carpeta(con, yo)
    ruta, _ = _ruta(carpeta, sistema, clave, nombre_fichero)
    if not ruta.is_file():
        raise Error("No hay partida ahí.", 404)
    return FileResponse(ruta, media_type="application/octet-stream", filename=ruta.name)


@app.get(PREFIJO + "/salud")
@app.get("/api/health")  # el HEALTHCHECK de la imagen (compartida con el servicio)
def salud():
    return {"ok": True}


# ---------------- línea de órdenes ----------------

def _consola_por_identificador(ident: str) -> tuple[sqlite3.Row, Path]:
    with _conectar() as con:
        fila = con.execute("SELECT * FROM consolas WHERE identificador = ?", (ident,)).fetchone()
        if fila is None:
            sys.exit("No hay ninguna consola con ese identificador.")
        return fila, _carpeta(con, fila)


def _cli_versiones(ident: str) -> None:
    _, carpeta = _consola_por_identificador(ident)
    base = carpeta / ".versiones"
    for d in sorted(p for p in base.rglob("*") if p.is_dir() and any(x.is_file() for x in p.iterdir())):
        marcas = sorted((float(x.name) for x in d.iterdir()), reverse=True)
        print(d.relative_to(base), " ".join(time.strftime("%Y-%m-%d %H:%M", time.localtime(m)) + f" [{m:.3f}]"
                                           for m in marcas))


def _cli_restaurar(ident: str, rel: str, version: str) -> None:
    _, carpeta = _consola_por_identificador(ident)
    sistema, clave, nombre = rel.split("/")
    ruta, rel_p = _ruta(carpeta, sistema, clave, nombre)
    vieja = carpeta / ".versiones" / rel_p / version
    if not vieja.is_file():
        sys.exit("No existe esa versión (ver: python partidas.py versiones <identificador>).")
    if ruta.is_file():
        _guardar_version(carpeta, rel_p, ruta)
    shutil.copy2(vieja, ruta)
    ahora = time.time()
    os.utime(ruta, (ahora, ahora))      # más nueva que la de las consolas: se la bajan
    print("Restaurada; las consolas la bajarán en su próxima sincronización.")


def _cli_migrar(db_libreria: str, saves_libreria: str) -> None:
    """Consolas, grupos, solicitudes, bloqueos y partidas de una librería."""
    vieja = sqlite3.connect(db_libreria)
    vieja.row_factory = sqlite3.Row
    with _conectar() as con:
        ids_grupo, ids_consola = {}, {}
        for g in vieja.execute("SELECT * FROM grupos"):
            existente = con.execute("SELECT id FROM grupos WHERE identificador = ?", (g["identificador"],)).fetchone()
            ids_grupo[g["id"]] = existente["id"] if existente else con.execute(
                "INSERT INTO grupos (identificador, creado) VALUES (?, ?)", (g["identificador"], g["creado"])).lastrowid
        for c in vieja.execute("SELECT * FROM consolas"):
            h = huella(c["android_id"])
            existente = con.execute("SELECT id FROM consolas WHERE huella = ?", (h,)).fetchone()
            if existente:
                ids_consola[c["id"]] = existente["id"]
                continue
            ids_consola[c["id"]] = con.execute(
                "INSERT INTO consolas (huella, identificador, nombre, grupo_id, creada, ultima_conexion) "
                "VALUES (?, ?, ?, ?, ?, ?)", (h, c["identificador"], c["nombre"], ids_grupo[c["grupo_id"]],
                                              c["creada"], c["ultima_conexion"])).lastrowid
            print("consola", c["identificador"], c["nombre"])
        for s in vieja.execute("SELECT * FROM solicitudes"):
            con.execute("INSERT OR IGNORE INTO solicitudes (origen_id, destino_id, creada) VALUES (?, ?, ?)",
                        (ids_consola[s["origen_id"]], ids_consola[s["destino_id"]], s["creada"]))
        for b in vieja.execute("SELECT * FROM bloqueos"):
            con.execute("INSERT OR IGNORE INTO bloqueos (bloqueador_id, bloqueado_id, creado) VALUES (?, ?, ?)",
                        (ids_consola[b["bloqueador_id"]], ids_consola[b["bloqueado_id"]], b["creado"]))
        for g in vieja.execute("SELECT * FROM grupos"):
            origen = Path(saves_libreria) / g["identificador"]
            if origen.is_dir():
                _fusionar_copia(origen, SAVES / g["identificador"])
    print("Migración terminada.")


def _fusionar_copia(origen: Path, destino: Path) -> None:
    for f in origen.rglob("*"):
        if f.is_file() and not f.name.endswith(".subiendo"):
            objetivo = destino / f.relative_to(origen)
            if not objetivo.exists() or objetivo.stat().st_mtime < f.stat().st_mtime:
                objetivo.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, objetivo)


if __name__ == "__main__":
    inicializar()
    ordenes = {"versiones": (_cli_versiones, 1), "restaurar": (_cli_restaurar, 3), "migrar": (_cli_migrar, 2)}
    if len(sys.argv) < 2 or sys.argv[1] not in ordenes or len(sys.argv) - 2 != ordenes[sys.argv[1]][1]:
        sys.exit(__doc__)
    ordenes[sys.argv[1]][0](*sys.argv[2:])
