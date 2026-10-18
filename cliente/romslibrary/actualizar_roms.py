"""
Actualiza los ROMs instalados cuando la librería tiene una versión más nueva
(igual que ActualizadorRoms en Android). La identidad es el SHA-256, no el
nombre: la librería manda las versiones SUPERADAS (sha + tamaño) y la actual.

- Solo se calcula el SHA de los ficheros cuyo tamaño coincide con alguna
  versión superada de su consola (con ISOs de varios GB, calcularlo de todo
  sería muy lento); cada SHA queda en caché por ruta + tamaño + fecha.
- Antes de tocar nada se suben sus partidas; la versión nueva se baja a un
  temporal, se comprueba su SHA y toma el MISMO nombre que tenía en la
  consola (los emuladores encuentran las partidas por el nombre del ROM).
- Nunca deja un juego a medias: el original se aparta y solo se borra
  cuando el nuevo está en su sitio.
"""

import json
import os
from pathlib import Path

from . import api, catalogo, guardados
from .ajustes import carpeta_datos
from .instalador import sha256

CACHE = carpeta_datos() / "hashes.json"


def _cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _sha_con_cache(cache: dict, ruta: Path) -> str:
    st = ruta.stat()
    firma = f"{st.st_size}|{int(st.st_mtime)}"
    guardado = cache.get(str(ruta))
    if guardado and guardado[0] == firma:
        return guardado[1]
    sha = sha256(ruta)
    cache[str(ruta)] = [firma, sha]
    return sha


def comprobar(ajustes, aviso=lambda texto: None) -> int:
    """Bloqueante. Devuelve cuántos juegos ha actualizado."""
    try:
        obsoletas = api.get_json(api.url_de(ajustes["url"], "versiones"), ajustes["clave"])["obsoletas"]
    except (api.ErrorApi, KeyError, TypeError):
        return 0
    por_sistema: dict[str, dict[int, list[dict]]] = {}
    for o in obsoletas:
        try:
            a = o["actual"]
            if o["sistema"] in ajustes.sistemas_visibles() and catalogo.componente_seguro(a["filename"]):
                por_sistema.setdefault(o["sistema"], {}).setdefault(int(o["tamano"]), []).append(o)
        except (KeyError, TypeError, ValueError):
            continue
    cache = _cache()
    hechas = 0
    for sistema, por_tamano in por_sistema.items():
        carpeta = ajustes.carpeta_sistema(sistema)
        if not carpeta.is_dir():
            continue
        for f in list(carpeta.iterdir()):
            if not f.is_file() or f.name.endswith((".descargando", ".anterior")):
                continue
            candidatas = por_tamano.get(f.stat().st_size)
            if not candidatas:
                continue
            sha = _sha_con_cache(cache, f)
            o = next((c for c in candidatas if c["sha"].lower() == sha), None)
            if o is None:
                continue
            aviso(f"Actualizando {f.name} a la versión {o['actual']['n']}…")
            if _actualizar(ajustes, f, sistema, o, cache):
                hechas += 1
                aviso(f"Actualizado: {f.name} (v{o['n']} → v{o['actual']['n']})")
    try:
        CACHE.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass
    return hechas


def _actualizar(ajustes, f: Path, sistema: str, o: dict, cache: dict) -> bool:
    a = o["actual"]
    sha_actual = a["sha"].lower()
    temporal = f.with_name(f.name + ".descargando")
    try:
        guardados.sincronizar_juego(ajustes, sistema, f.name)
        externa = a.get("fuenteExterna")
        if isinstance(externa, str) and externa.startswith("https://") and "@" not in externa:
            try:
                api.descargar(externa, str(temporal), reintentos=2)
            except api.ErrorApi:
                temporal.unlink(missing_ok=True)
        if not temporal.exists() or sha256(temporal) != sha_actual:
            temporal.unlink(missing_ok=True)
            origen = a.get("origenUrl")
            otra = isinstance(origen, str) and origen.startswith("https://") and "@" not in origen
            api.descargar(api.url_de(origen if otra else ajustes["url"], "descargar", system=sistema,
                                     filename=a["filename"]),
                          str(temporal), None if otra else ajustes["clave"])
        if sha256(temporal) != sha_actual:
            raise api.ErrorApi("la descarga no coincide con la versión publicada")
        apartado = f.with_name(f.name + ".anterior")
        os.replace(f, apartado)
        try:
            os.replace(temporal, f)
        except OSError:
            os.replace(apartado, f)
            raise
        apartado.unlink()
        cache[str(f)] = [f"{f.stat().st_size}|{int(f.stat().st_mtime)}", sha_actual]
        return True
    except (api.ErrorApi, OSError):
        temporal.unlink(missing_ok=True)
        return False
