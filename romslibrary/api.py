"""
Cliente HTTP de la API de RomsLibrary (/APIs/RomLibraryApp), la misma que usa
la app de Android. Solo biblioteca estándar: tiene que funcionar igual en una
consola con muOS que en Windows.

- La clave de acceso solo viaja a NUESTRO servidor (juegos de otra librería y
  enlaces de autores se piden sin ella).
- Descargas con reanudación (Range) y reintentos; nunca dan por buena una
  descarga más corta de lo anunciado.
"""

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

RUTA = "/APIs/RomLibraryApp"
AGENTE = "RomsLibrary-cliente"


class ErrorApi(Exception):
    def __init__(self, mensaje: str, codigo: int | None = None):
        super().__init__(mensaje)
        self.codigo = codigo


def _contexto_ssl():
    ctx = ssl.create_default_context()
    # Algunas consolas no traen certificados raíz en la ruta estándar.
    if not ctx.get_ca_certs():
        for ruta in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
                     "/etc/ssl/cert.pem"):
            if os.path.isfile(ruta):
                ctx.load_verify_locations(ruta)
                break
        else:
            try:                                        # los de Mozilla que van dentro del paquete
                import certifi
                ctx.load_verify_locations(certifi.where())
            except ImportError:
                pass
    return ctx


_SSL = _contexto_ssl()


def url_de(base: str, accion: str, **params) -> str:
    consulta = urllib.parse.urlencode({"accion": accion, **{k: v for k, v in params.items() if v is not None}})
    return f"{base.rstrip('/')}{RUTA}?{consulta}"


def _abrir(url: str, clave: str | None = None, datos: bytes | None = None, cabeceras: dict | None = None,
           timeout: float = 20):
    if not url.startswith("https://") and not url.startswith("http://"):
        raise ErrorApi(f"URL no válida: {url}")
    h = {"User-Agent": AGENTE}
    if clave:
        h["X-App-Token"] = clave
    h.update(cabeceras or {})
    req = urllib.request.Request(url, data=datos, headers=h)
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_SSL)
    except urllib.error.HTTPError as e:
        try:
            cuerpo = json.loads(e.read().decode("utf-8", "replace"))
            mensaje = cuerpo.get("message") or cuerpo.get("detail") or ""
        except Exception:  # noqa: BLE001
            mensaje = ""
        raise ErrorApi(mensaje or f"HTTP {e.code}", e.code) from None
    except (urllib.error.URLError, OSError) as e:
        raise ErrorApi(f"Sin conexión con {urllib.parse.urlparse(url).hostname}: {getattr(e, 'reason', e)}") from None


def consulta(**params) -> str:
    return urllib.parse.urlencode(params)


def get_json(url: str, clave: str | None = None, timeout: float = 20, cabeceras: dict | None = None):
    with _abrir(url, clave, cabeceras=cabeceras, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(url: str, datos: dict, cabeceras: dict | None = None):
    with _abrir(url, None, json.dumps(datos).encode(), {"Content-Type": "application/json", **(cabeceras or {})},
                timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def post_formulario(url: str, clave: str | None, campos: dict, fichero: tuple[str, str] | None = None,
                    cabeceras: dict | None = None):
    """POST multipart. fichero = (nombre, ruta local)."""
    limite = uuid.uuid4().hex
    partes = []
    for k, v in campos.items():
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    if fichero:
        nombre, ruta = fichero
        with open(ruta, "rb") as f:
            contenido = f.read()
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="fichero"; filename="{nombre}"\r\n'
                      f'Content-Type: application/octet-stream\r\n\r\n'.encode() + contenido + b"\r\n")
    cuerpo = b"".join(partes) + f"--{limite}--\r\n".encode()
    with _abrir(url, clave, cuerpo, {"Content-Type": f"multipart/form-data; boundary={limite}", **(cabeceras or {})},
                timeout=120) as r:
        texto = r.read().decode("utf-8")
        return json.loads(texto) if texto else None


def descargar(url: str, destino: str, clave: str | None = None, progreso=None, cancelado=None,
              reintentos: int = 6, cabeceras: dict | None = None) -> None:
    """Descarga `url` a `destino` reanudando tras cada corte. progreso(hecho, total)."""
    espera = 2
    for intento in range(reintentos + 1):
        hecho = os.path.getsize(destino) if os.path.exists(destino) else 0
        try:
            cab = {**(cabeceras or {}), **({"Range": f"bytes={hecho}-"} if hecho else {})}
            with _abrir(url, clave, cabeceras=cab, timeout=60) as r:
                if hecho and r.status != 206:
                    hecho = 0                              # el servidor no reanuda: desde cero
                longitud = r.headers.get("Content-Length")
                total = hecho + int(longitud) if longitud else -1
                with open(destino, "ab" if hecho else "wb") as salida:
                    while True:
                        if cancelado and cancelado():
                            raise ErrorApi("Cancelado")
                        trozo = r.read(1024 * 256)
                        if not trozo:
                            break
                        salida.write(trozo)
                        hecho += len(trozo)
                        if progreso:
                            progreso(hecho, total)
                if total >= 0 and hecho != total:
                    raise ErrorApi(f"Descarga incompleta ({hecho} de {total} bytes)")
                return
        except ErrorApi as e:
            if e.codigo == 416 and hecho:
                os.remove(destino)                         # lo parcial no cuadra con el fichero: desde cero
                continue
            if str(e) == "Cancelado" or intento == reintentos or (e.codigo and 400 <= e.codigo < 500):
                raise
            time.sleep(espera)
            espera = min(espera * 2, 30)
