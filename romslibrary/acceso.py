"""
«Solicitar acceso» a la librería conectada (ver servicio/app/accesos.py): la
app genera un token propio de esta consola y lo manda con su nombre e
identificador; el administrador la acepta o la rechaza en su gestor.
Aceptada, ese token pasa a ser la clave de esta consola en esa librería.
"""

import secrets

from . import api


def pendiente(ajustes) -> bool:
    return ajustes["url"] in (ajustes["accesos_pendientes"] or {})


def solicitar(ajustes) -> None:
    token = secrets.token_hex(24)
    api.post_json(api.url_de(ajustes["url"], "solicitar_acceso"),
                  {"token": token, "nombre": ajustes.nombre_consola or "Consola", "consola": ajustes.identificador or ""})
    ajustes["accesos_pendientes"] = {**(ajustes["accesos_pendientes"] or {}), ajustes["url"]: token}


def comprobar(ajustes) -> str | None:
    """"aceptada" (el token queda como clave), "rechazada" o None (pendiente o nada que mirar)."""
    pendientes = dict(ajustes["accesos_pendientes"] or {})
    token = pendientes.get(ajustes["url"])
    if not token:
        return None
    try:
        estado = api.get_json(api.url_de(ajustes["url"], "acceso_estado"), token).get("estado")
    except api.ErrorApi:
        return None
    if estado == "pendiente":
        return None
    del pendientes[ajustes["url"]]
    ajustes["accesos_pendientes"] = pendientes
    if estado == "aceptada":
        ajustes["clave"] = token
        return "aceptada"
    return "rechazada"
