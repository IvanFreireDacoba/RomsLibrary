"""
Registro de consolas y sincronización de guardados entre ellas (SQLite).

Cada consola se identifica de dos formas:
- android_id: el ANDROID_ID que ve la app. Es la "credencial": nunca se
  muestra al usuario ni se usa como nombre de carpeta.
- identificador: "XXXXX-XXXXX" numérico, único y público. Es lo que el
  usuario ve en la app y comparte para vincular consolas.
Para tocar guardados hay que presentar los DOS y deben corresponder a la
misma consola.

Los guardados no son de una consola sino de su GRUPO de sincronización
(saves/<identificador del grupo>/...): al principio cada consola tiene el
suyo propio; al vincular dos consolas (la segunda tiene que ACEPTAR) sus
carpetas se fusionan en una sola, quedándose siempre la copia más reciente
de cada fichero. Desvincular le da a la consola una copia de los guardados
actuales en un grupo nuevo; bloquear además impide volver a vincular hasta
que se desbloquee.
"""

import secrets
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import config

DB_PATH = config.DB_PATH
SAVES_ROOT = config.SAVES_ROOT

# Las operaciones que mueven consolas entre grupos (y fusionan/copian
# carpetas) no pueden solaparse entre sí.
_cerrojo_grupos = threading.Lock()


class ErrorConsola(Exception):
    """Error de negocio con código HTTP sugerido."""

    def __init__(self, mensaje: str, codigo: int = 400):
        super().__init__(mensaje)
        self.codigo = codigo


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def inicializar() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conectar() as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS grupos (
                id INTEGER PRIMARY KEY,
                identificador TEXT NOT NULL UNIQUE,
                creado REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS consolas (
                id INTEGER PRIMARY KEY,
                android_id TEXT NOT NULL UNIQUE,
                identificador TEXT NOT NULL UNIQUE,
                nombre TEXT NOT NULL,
                grupo_id INTEGER NOT NULL REFERENCES grupos(id),
                creada REAL NOT NULL,
                ultima_conexion REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS solicitudes (
                id INTEGER PRIMARY KEY,
                origen_id INTEGER NOT NULL REFERENCES consolas(id) ON DELETE CASCADE,
                destino_id INTEGER NOT NULL REFERENCES consolas(id) ON DELETE CASCADE,
                creada REAL NOT NULL,
                UNIQUE (origen_id, destino_id)
            );
            CREATE TABLE IF NOT EXISTS bloqueos (
                bloqueador_id INTEGER NOT NULL REFERENCES consolas(id) ON DELETE CASCADE,
                bloqueado_id INTEGER NOT NULL REFERENCES consolas(id) ON DELETE CASCADE,
                creado REAL NOT NULL,
                PRIMARY KEY (bloqueador_id, bloqueado_id)
            );
            """
        )


# ---------- Identificadores ----------

def _nuevo_identificador(con: sqlite3.Connection, tabla: str) -> str:
    """XXXXX-XXXXX numérico al azar (00000-00001 .. 99999-99999), único en la tabla."""
    while True:
        n = 1 + secrets.randbelow(10**10 - 1)
        ident = f"{n // 10**5:05d}-{n % 10**5:05d}"
        if not con.execute(f"SELECT 1 FROM {tabla} WHERE identificador = ?", (ident,)).fetchone():
            return ident


def formato_identificador_valido(ident: str) -> bool:
    return len(ident) == 11 and ident[5] == "-" and (ident[:5] + ident[6:]).isdigit() and ident != "00000-00000"


def _carpeta_grupo(identificador_grupo: str) -> Path:
    return SAVES_ROOT / identificador_grupo


def _nuevo_grupo(con: sqlite3.Connection) -> sqlite3.Row:
    ident = _nuevo_identificador(con, "grupos")
    cur = con.execute("INSERT INTO grupos (identificador, creado) VALUES (?, ?)", (ident, time.time()))
    return con.execute("SELECT * FROM grupos WHERE id = ?", (cur.lastrowid,)).fetchone()


# ---------- Registro y autenticación ----------

def _limpiar_nombre(nombre: str) -> str:
    nombre = " ".join((nombre or "").split())[:40]
    if not nombre:
        raise ErrorConsola("Ponle un nombre a la consola.")
    return nombre


def registrar(android_id: str, nombre: str) -> dict:
    """Primera configuración de la app. Si la consola ya existía (reinstalar
    la app), devuelve sus datos sin cambiar nada. La primera vez, los
    guardados que tuviera en el formato antiguo (saves/<android_id>/) pasan
    a la carpeta de su grupo nuevo."""
    android_id = (android_id or "").strip()
    if not android_id or "/" in android_id or len(android_id) > 64:
        raise ErrorConsola("ANDROID_ID inválido.")
    with _cerrojo_grupos, _conectar() as con:
        fila = con.execute("SELECT * FROM consolas WHERE android_id = ?", (android_id,)).fetchone()
        if fila is None:
            nombre = _limpiar_nombre(nombre)
            grupo = _nuevo_grupo(con)
            ident = _nuevo_identificador(con, "consolas")
            ahora = time.time()
            con.execute(
                "INSERT INTO consolas (android_id, identificador, nombre, grupo_id, creada, ultima_conexion)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (android_id, ident, nombre, grupo["id"], ahora, ahora),
            )
            antigua = SAVES_ROOT / android_id
            if antigua.is_dir():
                antigua.rename(_carpeta_grupo(grupo["identificador"]))
            fila = con.execute("SELECT * FROM consolas WHERE android_id = ?", (android_id,)).fetchone()
        return {"identificador": fila["identificador"], "nombre": fila["nombre"]}


def autenticar(android_id: str, identificador: str) -> sqlite3.Row:
    with _conectar() as con:
        fila = con.execute(
            "SELECT * FROM consolas WHERE android_id = ? AND identificador = ?",
            ((android_id or "").strip(), (identificador or "").strip()),
        ).fetchone()
        if fila is None:
            raise ErrorConsola("Consola no autorizada.", 403)
        con.execute("UPDATE consolas SET ultima_conexion = ? WHERE id = ?", (time.time(), fila["id"]))
        return fila


def carpeta_guardados(consola: sqlite3.Row) -> Path:
    with _conectar() as con:
        grupo = con.execute("SELECT identificador FROM grupos WHERE id = ?", (consola["grupo_id"],)).fetchone()
    return _carpeta_grupo(grupo["identificador"])


def renombrar(consola: sqlite3.Row, nombre: str) -> dict:
    nombre = _limpiar_nombre(nombre)
    with _conectar() as con:
        con.execute("UPDATE consolas SET nombre = ? WHERE id = ?", (nombre, consola["id"]))
    return {"identificador": consola["identificador"], "nombre": nombre}


# ---------- Estado para la pantalla "Sincronizar consolas" ----------

def _resumen(fila: sqlite3.Row) -> dict:
    return {"nombre": fila["nombre"], "identificador": fila["identificador"]}


def estado(consola: sqlite3.Row) -> dict:
    with _conectar() as con:
        sincronizadas = con.execute(
            "SELECT * FROM consolas WHERE grupo_id = ? AND id != ? ORDER BY nombre",
            (consola["grupo_id"], consola["id"]),
        ).fetchall()
        recibidas = con.execute(
            "SELECT s.id AS solicitud, c.* FROM solicitudes s JOIN consolas c ON c.id = s.origen_id"
            " WHERE s.destino_id = ? ORDER BY s.creada",
            (consola["id"],),
        ).fetchall()
        enviadas = con.execute(
            "SELECT s.id AS solicitud, c.* FROM solicitudes s JOIN consolas c ON c.id = s.destino_id"
            " WHERE s.origen_id = ? ORDER BY s.creada",
            (consola["id"],),
        ).fetchall()
        bloqueadas = con.execute(
            "SELECT c.* FROM bloqueos b JOIN consolas c ON c.id = b.bloqueado_id WHERE b.bloqueador_id = ? ORDER BY c.nombre",
            (consola["id"],),
        ).fetchall()
    return {
        "yo": _resumen(consola),
        "sincronizadas": [_resumen(f) for f in sincronizadas],
        "solicitudes_recibidas": [{"solicitud": f["solicitud"], **_resumen(f)} for f in recibidas],
        "solicitudes_enviadas": [{"solicitud": f["solicitud"], **_resumen(f)} for f in enviadas],
        "bloqueadas": [_resumen(f) for f in bloqueadas],
    }


# ---------- Vincular / aceptar / desvincular / bloquear ----------

def _consola_por_identificador(con: sqlite3.Connection, ident: str) -> sqlite3.Row:
    ident = (ident or "").strip()
    if not formato_identificador_valido(ident):
        raise ErrorConsola("El identificador tiene que tener el formato 12345-67890.")
    fila = con.execute("SELECT * FROM consolas WHERE identificador = ?", (ident,)).fetchone()
    if fila is None:
        raise ErrorConsola("No hay ninguna consola con ese identificador.", 404)
    return fila


def _bloqueado(con: sqlite3.Connection, bloqueador_id: int, bloqueado_id: int) -> bool:
    return con.execute(
        "SELECT 1 FROM bloqueos WHERE bloqueador_id = ? AND bloqueado_id = ?", (bloqueador_id, bloqueado_id)
    ).fetchone() is not None


def solicitar(consola: sqlite3.Row, identificador_destino: str) -> dict:
    with _conectar() as con:
        destino = _consola_por_identificador(con, identificador_destino)
        if destino["id"] == consola["id"]:
            raise ErrorConsola("Ese es el identificador de esta misma consola.")
        if destino["grupo_id"] == consola["grupo_id"]:
            raise ErrorConsola("Esa consola ya está sincronizada con esta.")
        if _bloqueado(con, consola["id"], destino["id"]):
            raise ErrorConsola("Tienes esa consola bloqueada: desbloquéala antes en «Consolas bloqueadas».")
        if not _bloqueado(con, destino["id"], consola["id"]):
            # Si la otra consola nos tiene bloqueados no se le notifica nada,
            # pero tampoco se le dice a quien pide (no revela el bloqueo).
            con.execute(
                "INSERT OR IGNORE INTO solicitudes (origen_id, destino_id, creada) VALUES (?, ?, ?)",
                (consola["id"], destino["id"], time.time()),
            )
        return {"ok": True, "pendiente_de": _resumen(destino)}


def _fusionar_carpetas(origen: Path, destino: Path) -> None:
    """Mueve todo origen dentro de destino; si un fichero existe en los dos,
    gana el más reciente (mtime). Al final origen desaparece."""
    if not origen.is_dir():
        return
    destino.mkdir(parents=True, exist_ok=True)
    for f in sorted(origen.rglob("*")):
        if not f.is_file():
            continue
        objetivo = destino / f.relative_to(origen)
        if objetivo.exists() and objetivo.stat().st_mtime >= f.stat().st_mtime:
            continue
        objetivo.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(objetivo))
    shutil.rmtree(origen, ignore_errors=True)


def responder(consola: sqlite3.Row, solicitud_id: int, aceptar: bool) -> dict:
    with _cerrojo_grupos, _conectar() as con:
        sol = con.execute(
            "SELECT * FROM solicitudes WHERE id = ? AND destino_id = ?", (solicitud_id, consola["id"])
        ).fetchone()
        if sol is None:
            raise ErrorConsola("Esa solicitud ya no existe.", 404)
        origen = con.execute("SELECT * FROM consolas WHERE id = ?", (sol["origen_id"],)).fetchone()
        con.execute(
            "DELETE FROM solicitudes WHERE (origen_id = ? AND destino_id = ?) OR (origen_id = ? AND destino_id = ?)",
            (origen["id"], consola["id"], consola["id"], origen["id"]),
        )
        if not aceptar:
            return {"ok": True, "aceptada": False}
        # El grupo de quien acepta se une al de quien pidió: sus guardados se
        # fusionan en una sola carpeta (gana siempre el más reciente).
        grupo_destino = con.execute("SELECT * FROM grupos WHERE id = ?", (origen["grupo_id"],)).fetchone()
        grupo_viejo = con.execute(
            "SELECT * FROM grupos WHERE id = (SELECT grupo_id FROM consolas WHERE id = ?)", (consola["id"],)
        ).fetchone()
        if grupo_viejo["id"] != grupo_destino["id"]:
            _fusionar_carpetas(_carpeta_grupo(grupo_viejo["identificador"]), _carpeta_grupo(grupo_destino["identificador"]))
            con.execute("UPDATE consolas SET grupo_id = ? WHERE grupo_id = ?", (grupo_destino["id"], grupo_viejo["id"]))
            con.execute("DELETE FROM grupos WHERE id = ?", (grupo_viejo["id"],))
        return {"ok": True, "aceptada": True, "con": _resumen(origen)}


def _sacar_del_grupo(con: sqlite3.Connection, consola_id: int) -> None:
    """La consola pasa a un grupo nuevo propio con una COPIA de los
    guardados actuales del grupo (no pierde su progreso)."""
    fila = con.execute("SELECT * FROM consolas WHERE id = ?", (consola_id,)).fetchone()
    grupo = con.execute("SELECT * FROM grupos WHERE id = ?", (fila["grupo_id"],)).fetchone()
    nuevo = _nuevo_grupo(con)
    actual = _carpeta_grupo(grupo["identificador"])
    if actual.is_dir():
        shutil.copytree(actual, _carpeta_grupo(nuevo["identificador"]))  # copy2: conserva las fechas
    con.execute("UPDATE consolas SET grupo_id = ? WHERE id = ?", (nuevo["id"], consola_id))


def desincronizar(consola: sqlite3.Row, identificador_otra: str) -> dict:
    with _cerrojo_grupos, _conectar() as con:
        otra = _consola_por_identificador(con, identificador_otra)
        actual = con.execute("SELECT grupo_id FROM consolas WHERE id = ?", (consola["id"],)).fetchone()
        if otra["id"] == consola["id"] or otra["grupo_id"] != actual["grupo_id"]:
            raise ErrorConsola("Esa consola no está sincronizada con esta.")
        _sacar_del_grupo(con, otra["id"])
        return {"ok": True}


def bloquear(consola: sqlite3.Row, identificador_otra: str) -> dict:
    with _cerrojo_grupos, _conectar() as con:
        otra = _consola_por_identificador(con, identificador_otra)
        if otra["id"] == consola["id"]:
            raise ErrorConsola("No puedes bloquear esta misma consola.")
        actual = con.execute("SELECT grupo_id FROM consolas WHERE id = ?", (consola["id"],)).fetchone()
        if otra["grupo_id"] == actual["grupo_id"]:
            _sacar_del_grupo(con, otra["id"])
        con.execute(
            "DELETE FROM solicitudes WHERE (origen_id = ? AND destino_id = ?) OR (origen_id = ? AND destino_id = ?)",
            (otra["id"], consola["id"], consola["id"], otra["id"]),
        )
        con.execute(
            "INSERT OR IGNORE INTO bloqueos (bloqueador_id, bloqueado_id, creado) VALUES (?, ?, ?)",
            (consola["id"], otra["id"], time.time()),
        )
        return {"ok": True}


def desbloquear(consola: sqlite3.Row, identificador_otra: str) -> dict:
    with _conectar() as con:
        otra = _consola_por_identificador(con, identificador_otra)
        con.execute("DELETE FROM bloqueos WHERE bloqueador_id = ? AND bloqueado_id = ?", (consola["id"], otra["id"]))
        return {"ok": True}


def cancelar_solicitud(consola: sqlite3.Row, solicitud_id: int) -> dict:
    with _conectar() as con:
        con.execute("DELETE FROM solicitudes WHERE id = ? AND origen_id = ?", (solicitud_id, consola["id"]))
        return {"ok": True}
