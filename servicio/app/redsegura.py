"""
Peticiones del servidor a direcciones que da un usuario o un tercero (alta de
librerías en el índice, enlaces de descarga externos): solo a direcciones
PÚBLICAS por https. Nunca loopback, red privada, Tailscale/CGNAT, link-local...
Sin esto se le podría hacer pedir URLs de la red interna (SSRF). En las
descargas se comprueba también cada redirección.
"""

import ipaddress
import socket
from urllib.parse import urlparse

import httpx


def url_publica(url: str) -> bool:
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


async def _comprobar(peticion: httpx.Request) -> None:
    if not url_publica(str(peticion.url)):
        raise httpx.RequestError(f"Dirección no permitida: {peticion.url.host}", request=peticion)


def cliente(**opciones) -> httpx.AsyncClient:
    """Cliente que sigue redirecciones pero solo hacia direcciones públicas https."""
    return httpx.AsyncClient(follow_redirects=True, max_redirects=5,
                             event_hooks={"request": [_comprobar]}, **opciones)
