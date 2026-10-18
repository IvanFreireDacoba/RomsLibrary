"""
Servicio de partidas (sund.es/partidas): identidad de la consola, sus partidas
y la vinculación con otras consolas, aparte de la librería de la que vengan
los juegos (ver servicio/app/partidas.py).

La credencial de la consola (ajustes["credencial"], lo que en Android es el
ANDROID_ID) es su contraseña: solo viaja aquí, para abrir una sesión. Lo
demás va con el token de la sesión; si caduca (401), se abre otra sola.
"""

import sys

from . import api

PREFIJO = "/partidas/v1"


def _url(ajustes, ruta: str) -> str:
    return ajustes["url_partidas"].rstrip("/") + PREFIJO + ruta


def _abrir_sesion(ajustes, nombre: str | None = None) -> None:
    datos = {"secreto": ajustes["credencial"]}
    if nombre:
        datos["nombre"] = nombre
    r = api.post_json(_url(ajustes, "/sesion"), datos)
    ajustes.guardar_cuenta(r["identificador"], r["nombre"], r["token"])


def _con_sesion(ajustes, peticion):
    """peticion(cabeceras) con la sesión guardada; si ha caducado, abre otra."""
    if ajustes["token_partidas"]:
        try:
            return peticion({"Authorization": "Bearer " + ajustes["token_partidas"]})
        except api.ErrorApi as e:
            if e.codigo != 401:
                raise
    _abrir_sesion(ajustes)
    return peticion({"Authorization": "Bearer " + ajustes["token_partidas"]})


def nombre_maquina() -> str:
    import platform as p
    return (p.node() or ("PC" if sys.platform == "win32" else "Linux"))[:40]


def registrar(ajustes, nombre: str = "") -> None:
    """Registra la consola (o entra si ya lo estaba) y le pone ese nombre."""
    nombre = nombre.strip() or ajustes.nombre_consola or nombre_maquina()
    _abrir_sesion(ajustes, nombre)
    if nombre != ajustes.nombre_consola:
        renombrar(ajustes, nombre)


def recuperar(ajustes) -> None:
    """Sin identidad guardada: entra si la consola ya existía (reinstalación o
    migración desde las partidas de la librería) o la registra."""
    try:
        _abrir_sesion(ajustes)
    except api.ErrorApi as e:
        if e.codigo != 404:
            raise
        registrar(ajustes, ajustes.nombre_antiguo())


def renombrar(ajustes, nombre: str) -> None:
    r = accion(ajustes, "renombrar", nombre=nombre.strip())
    ajustes.guardar_cuenta(ajustes.identificador, r["nombre"], ajustes["token_partidas"])


def estado(ajustes) -> dict:
    return _con_sesion(ajustes, lambda cab: api.get_json(_url(ajustes, "/estado"), cabeceras=cab))


def accion(ajustes, op: str, **datos):
    return _con_sesion(ajustes, lambda cab: api.post_json(_url(ajustes, "/consola/" + op), datos, cab))


def indice(ajustes) -> list[dict]:
    return _con_sesion(ajustes, lambda cab: api.get_json(_url(ajustes, "/indice"), cabeceras=cab))


def subir(ajustes, campos: dict, nombre: str, ruta_local: str) -> None:
    _con_sesion(ajustes, lambda cab: api.post_formulario(_url(ajustes, "/partida"), None, campos,
                                                         (nombre, ruta_local), cab))


def bajar(ajustes, sistema: str, clave: str, nombre: str, destino: str) -> None:
    url = _url(ajustes, "/partida?" + api.consulta(sistema=sistema, clave=clave, nombre_fichero=nombre))
    _con_sesion(ajustes, lambda cab: api.descargar(url, destino, cabeceras=cab))
