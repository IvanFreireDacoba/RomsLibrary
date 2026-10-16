"""
Acceso de consolas a la librería completa (sus juegos privados), sin repartir
la clave de la librería.

Desde la app, «Solicitar acceso» manda el nombre y el identificador de la
consola (los del servicio de partidas, solo para que el administrador la
reconozca) y un TOKEN aleatorio que la app genera y guarda. El administrador
ve la solicitud en el gestor y la acepta o la rechaza. Aceptada, ese token
vale como clave de acceso SOLO para esa consola (cabecera X-App-Token) y se
puede revocar en cualquier momento sin tocar a las demás.

Aquí solo se guarda el hash del token. Las rechazadas se recuerdan 30 días
(para que la app lo sepa) y las pendientes caducan a los 30 días. No se
guardan direcciones IP: el tope de solicitudes por conexión vive en memoria.
"""

import hashlib
import sqlite3
import threading
import time

import config

PENDIENTES_MAX = 50                  # en total, para que nadie llene la lista
SOLICITUDES_POR_IP_DIA = 5
CADUCIDAD = 30 * 86400

_por_ip: dict[str, list[float]] = {}
_cerrojo = threading.Lock()


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def inicializar() -> None:
    with _conectar() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS accesos (
                id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE, nombre TEXT NOT NULL,
                consola TEXT NOT NULL DEFAULT '', estado TEXT NOT NULL, creada REAL NOT NULL,
                decidida REAL, usuario TEXT NOT NULL DEFAULT '', ultimo_uso REAL
            )""")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _limpiar(con) -> None:
    con.execute("DELETE FROM accesos WHERE estado IN ('pendiente', 'rechazada') AND creada < ?",
                (time.time() - CADUCIDAD,))


class Error(Exception):
    pass


def solicitar(token: str, nombre: str, consola: str, ip: str) -> dict:
    token = str(token or "")
    if not 32 <= len(token) <= 128:
        raise Error("Solicitud inválida.")
    nombre = " ".join(str(nombre or "").split())[:40] or "Consola sin nombre"
    consola = str(consola or "")[:11] if len(str(consola or "")) == 11 else ""
    ahora = time.time()
    with _cerrojo:
        marcas = [t for t in _por_ip.get(ip, []) if ahora - t < 86400]
        if len(marcas) >= SOLICITUDES_POR_IP_DIA:
            raise Error("Demasiadas solicitudes desde esta conexión. Prueba mañana.")
        _por_ip[ip] = marcas + [ahora]
        if len(_por_ip) > 10000:
            _por_ip.clear()
    with _conectar() as con:
        _limpiar(con)
        if con.execute("SELECT 1 FROM accesos WHERE token = ?", (_hash(token),)).fetchone():
            return {"estado": estado(token)}
        if con.execute("SELECT COUNT(*) FROM accesos WHERE estado = 'pendiente'").fetchone()[0] >= PENDIENTES_MAX:
            raise Error("Esta librería tiene demasiadas solicitudes pendientes.")
        con.execute("INSERT INTO accesos (token, nombre, consola, estado, creada) VALUES (?, ?, ?, 'pendiente', ?)",
                    (_hash(token), nombre, consola, ahora))
    return {"estado": "pendiente"}


def estado(token: str) -> str:
    """pendiente | aceptada | rechazada | desconocida (revocada o caducada)."""
    with _conectar() as con:
        fila = con.execute("SELECT estado FROM accesos WHERE token = ?", (_hash(str(token or "")),)).fetchone()
    return fila["estado"] if fila else "desconocida"


def valido(token: str) -> bool:
    if not token or len(token) < 32:
        return False
    with _conectar() as con:
        cur = con.execute("UPDATE accesos SET ultimo_uso = ? WHERE token = ? AND estado = 'aceptada'",
                          (time.time(), _hash(token)))
        return cur.rowcount > 0


def listar() -> list[dict]:
    with _conectar() as con:
        _limpiar(con)
        filas = con.execute("SELECT id, nombre, consola, estado, creada, decidida, usuario, ultimo_uso FROM accesos "
                            "WHERE estado != 'rechazada' ORDER BY estado DESC, creada").fetchall()
    return [dict(f) for f in filas]


def decidir(id_: int, accion: str, usuario: str) -> None:
    """aceptar | rechazar (pendientes) · revocar (aceptadas: se borra)."""
    with _conectar() as con:
        if accion == "revocar":
            con.execute("DELETE FROM accesos WHERE id = ? AND estado = 'aceptada'", (id_,))
        elif accion in ("aceptar", "rechazar"):
            con.execute("UPDATE accesos SET estado = ?, decidida = ?, usuario = ? WHERE id = ? AND estado = 'pendiente'",
                        ("aceptada" if accion == "aceptar" else "rechazada", time.time(), usuario, id_))
        else:
            raise Error("Acción desconocida.")
