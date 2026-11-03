"""
Caché de metadatos de los juegos de varios ficheros (carpetas de grupo).

Listar el catálogo, los hashes o las versiones NUNCA debe recorrer a fondo
el disco ni esperar a una descompresión: un juego de RPG Maker tiene miles
de ficheros y, desde el almacenamiento comprimido de Switch (comprimidos.py),
el tamaño lógico sale de la base de datos de cada .nsz/.xcz. Hacerlo en cada
petición llegó a costar 3-6 s por listado, y como varias peticiones del
arranque de la app iban seguidas, la app cortaba por timeout.

Aquí se guarda, por carpeta de grupo:
- tamano: lo que ocupa descomprimido (lo que se descarga).
- firma: (bytes en disco, mtime más reciente), sin grupo.json, como
  federacion._firma, para casar con la tabla de hashes.

Se sirve lo último conocido (al instante); pasados TTL segundos se
recalcula en segundo plano (un solo hilo, sin repetir trabajo), por si ha
cambiado algo en una subcarpeta. Solo se calcula en el momento la primera
vez que se ve una carpeta o si su raíz / su grupo.json han cambiado de
mtime (lo que pasa al subir, actualizar, comprimir o renombrar).
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import comprimidos

TTL = 300   # además, cualquier cambio en la raíz del grupo se nota al momento (ver _marca)

_datos: dict[str, dict] = {}
_candado = threading.Lock()
_pendientes: set[str] = set()
_hilo = ThreadPoolExecutor(max_workers=1, thread_name_prefix="metacache")


def _marca(carpeta: Path) -> tuple:
    """Barata (2 stat): cambia al añadir/quitar/renombrar en la raíz del grupo."""
    try:
        m = carpeta.stat().st_mtime_ns
    except OSError:
        m = None
    try:
        g = (carpeta / "grupo.json").stat().st_mtime_ns
    except OSError:
        g = None
    return m, g


def calcular(carpeta: Path) -> dict:
    """Recorrido completo (lento): solo desde el hilo de fondo o la primera vez."""
    marca = _marca(carpeta)
    tamano = comprimidos.tamano_juego(carpeta)
    fisicos = [p for p in carpeta.rglob("*") if p.is_file() and p.name != "grupo.json"]
    stats = [p.stat() for p in fisicos]
    firma = (sum(s.st_size for s in stats), max((s.st_mtime for s in stats), default=0))
    d = {"tamano": tamano, "firma": firma, "marca": marca, "cuando": time.monotonic()}
    with _candado:
        _datos[str(carpeta)] = d
    return d


def _refrescar(clave: str) -> None:
    try:
        carpeta = Path(clave)
        if carpeta.is_dir():
            calcular(carpeta)
        else:
            with _candado:
                _datos.pop(clave, None)
    except OSError:
        pass
    finally:
        with _candado:
            _pendientes.discard(clave)


def _obtener(carpeta: Path) -> dict:
    clave = str(carpeta)
    with _candado:
        d = _datos.get(clave)
    if d is None or _marca(carpeta) != d["marca"]:
        # Nuevo o cambiado en su raíz (subida, update, comprimido, renombrado):
        # se recalcula ya, para no dar nunca una firma que ya no casa.
        return calcular(carpeta)
    if time.monotonic() - d["cuando"] > TTL:
        with _candado:
            if clave not in _pendientes:
                _pendientes.add(clave)
                _hilo.submit(_refrescar, clave)
    return d


def tamano(carpeta: Path) -> int:
    return _obtener(carpeta)["tamano"]


def firma(carpeta: Path) -> tuple[int, float]:
    return _obtener(carpeta)["firma"]


def invalidar(carpeta: Path) -> None:
    with _candado:
        _datos.pop(str(carpeta), None)


def precalentar(raiz: Path) -> None:
    """Al arrancar el servicio: calcula todas las carpetas de grupo para que
    ni el primer listado tenga que recorrerlas."""
    def trabajo():
        try:
            for sistema in sorted(p for p in raiz.iterdir() if p.is_dir()):
                for juego in sorted(sistema.iterdir()):
                    if juego.is_dir() and not juego.name.startswith("."):
                        try:
                            calcular(juego)
                        except OSError:
                            pass
        except OSError:
            pass
        print(f"[metacache] {len(_datos)} grupos en caché", flush=True)
    threading.Thread(target=trabajo, name="metacache-inicio", daemon=True).start()
