"""
Instalar y desinstalar juegos en esta consola.

- Un fichero: se baja a "<nombre>.descargando" junto a su sitio y solo al
  terminar entero toma su nombre (nunca queda un juego a medias con el nombre
  bueno). Si el juego tiene enlace del autor se prueba primero de ahí y vale
  solo si el SHA coincide; si no, de la librería.
- Varios ficheros (PS1 cue+bin, multidisco, Switch): fichero a fichero dentro
  de su carpeta; si uno falla se borra la carpeta entera.
- RPG Maker (solo Windows: Game.exe): .zip descomprimido en su carpeta, sin
  entradas que se salgan de ella.
- Antes de desinstalar se suben las partidas (guardados.py).
"""

import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from . import api, catalogo


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        while trozo := f.read(4 * 1024 * 1024):
            h.update(trozo)
    return h.hexdigest()


def _url_descarga(ajustes, j: catalogo.Juego) -> str:
    return api.url_de(j.base(ajustes["url"]), "descargar", system=j.sistema, filename=j.fichero)


def _de_fuente_externa(j: catalogo.Juego, destino: Path, progreso, cancelado) -> bool:
    if not j.fuente_externa or not j.sha_enlace:
        return False
    try:
        api.descargar(j.fuente_externa, str(destino), None, progreso, cancelado, reintentos=2)
        if sha256(destino) == j.sha_enlace:
            return True
    except api.ErrorApi as e:
        if str(e) == "Cancelado":
            raise
    destino.unlink(missing_ok=True)
    return False


def instalar(ajustes, j: catalogo.Juego, progreso=None, cancelado=None) -> None:
    destino = catalogo.ruta_local(ajustes, j)
    destino.parent.mkdir(parents=True, exist_ok=True)
    clave = j.clave(ajustes["clave"])
    if j.sistema == "rpgmaker":
        _instalar_zip(ajustes, j, destino, clave, progreso, cancelado)
    elif j.es_grupo:
        _instalar_grupo(ajustes, j, destino, clave, progreso, cancelado)
    else:
        parcial = destino.with_name(destino.name + ".descargando")
        if not _de_fuente_externa(j, parcial, progreso, cancelado):
            api.descargar(_url_descarga(ajustes, j), str(parcial), clave, progreso, cancelado)
        os.replace(parcial, destino)


def _instalar_grupo(ajustes, j, destino: Path, clave, progreso, cancelado) -> None:
    base = j.base(ajustes["url"])
    ficheros = api.get_json(api.url_de(base, "grupo_ficheros", system=j.sistema, grupo=j.fichero), clave)
    total = sum(int(f.get("tamano") or 0) for f in ficheros) or -1
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)
    hecho_antes = 0
    try:
        for f in ficheros:
            partes = str(f.get("nombre", "")).split("/")
            if not all(catalogo.componente_seguro(p) for p in partes):
                raise api.ErrorApi(f"Nombre de fichero no válido en el grupo: {f.get('nombre')}")
            local = destino.joinpath(*partes)
            local.parent.mkdir(parents=True, exist_ok=True)
            url = api.url_de(base, "grupo_fichero", system=j.sistema, grupo=j.fichero, fichero=f["nombre"])
            api.descargar(url, str(local), clave,
                          (lambda h, t, a=hecho_antes: progreso(a + h, total)) if progreso else None, cancelado)
            hecho_antes += int(f.get("tamano") or 0)
    except BaseException:
        shutil.rmtree(destino, ignore_errors=True)
        raise


def _instalar_zip(ajustes, j, destino: Path, clave, progreso, cancelado) -> None:
    descriptor, temporal = tempfile.mkstemp(suffix=".zip")
    os.close(descriptor)
    temporal = Path(temporal)
    try:
        temporal.unlink()
        if not _de_fuente_externa(j, temporal, progreso, cancelado):
            api.descargar(_url_descarga(ajustes, j), str(temporal), clave, progreso, cancelado)
        if destino.exists():
            shutil.rmtree(destino)
        destino.mkdir(parents=True)
        raiz = destino.resolve()
        try:
            with zipfile.ZipFile(temporal) as zf:
                for miembro in zf.infolist():
                    final = (destino / miembro.filename).resolve()
                    if not final.is_relative_to(raiz):
                        raise api.ErrorApi(f"Zip con una ruta insegura: {miembro.filename}")
                zf.extractall(destino)
        except (zipfile.BadZipFile, api.ErrorApi):
            shutil.rmtree(destino, ignore_errors=True)
            raise
    finally:
        temporal.unlink(missing_ok=True)


def desinstalar(ajustes, j: catalogo.Juego) -> None:
    ruta = catalogo.ruta_local(ajustes, j)
    if ruta.is_dir():
        shutil.rmtree(ruta)
    elif ruta.exists():
        ruta.unlink()


def descargar_portada(ajustes, j: catalogo.Juego, cache: Path) -> Path | None:
    if not j.portada:
        return None
    nombre = hashlib.sha1(j.portada.encode()).hexdigest()[:20]
    ruta = cache / nombre
    if ruta.is_file() and ruta.stat().st_size > 0:
        return ruta
    cache.mkdir(parents=True, exist_ok=True)
    parcial = ruta.with_suffix(".parcial")
    try:
        api.descargar(j.portada, str(parcial), j.clave(ajustes["clave"]), reintentos=1)
        os.replace(parcial, ruta)
        return ruta
    except api.ErrorApi:
        parcial.unlink(missing_ok=True)
        return None
