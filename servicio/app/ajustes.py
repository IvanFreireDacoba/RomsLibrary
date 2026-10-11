"""
Ajustes del servicio que se cambian desde el gestor web (en la BD, no en
variables de entorno: así se pueden tocar sin reiniciar el contenedor).

- nombre: cómo se llama esta librería (se ve en otras librerías públicas).
- url_publica: dirección por la que otros (la app, otras librerías) llegan
  a este servicio, p.ej. https://sund.es.
- publico: "1" = librería PÚBLICA (comparte sus juegos y ve los de otras
  librerías públicas), "0" = PRIVADA (solo los suyos). Por defecto pública.
- clave_app: clave de acceso que hay que poner en la app para conectarse.
"""

import secrets
import sqlite3

import config

ESQUEMA = "CREATE TABLE IF NOT EXISTS ajustes (clave TEXT PRIMARY KEY, valor TEXT NOT NULL)"


def _conectar() -> sqlite3.Connection:
    return sqlite3.connect(config.DB_PATH, timeout=15, isolation_level=None)


def inicializar() -> None:
    defectos = {
        "nombre": config.NOMBRE_INICIAL,
        "url_publica": config.URL_PUBLICA_INICIAL,
        "publico": "1" if config.PUBLICO_INICIAL else "0",
        "clave_app": config.CLAVE_APP_INICIAL or secrets.token_urlsafe(24),
    }
    with _conectar() as con:
        con.execute(ESQUEMA)
        for clave, valor in defectos.items():
            con.execute("INSERT OR IGNORE INTO ajustes (clave, valor) VALUES (?, ?)", (clave, valor))


def leer(clave: str, defecto: str = "") -> str:
    with _conectar() as con:
        fila = con.execute("SELECT valor FROM ajustes WHERE clave = ?", (clave,)).fetchone()
    return fila[0] if fila else defecto


def guardar(clave: str, valor: str) -> None:
    with _conectar() as con:
        con.execute("INSERT INTO ajustes (clave, valor) VALUES (?, ?) "
                    "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (clave, valor))


def todos() -> dict:
    with _conectar() as con:
        return dict(con.execute("SELECT clave, valor FROM ajustes").fetchall())


def publico() -> bool:
    return leer("publico", "1") == "1"
