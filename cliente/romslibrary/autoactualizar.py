"""
La app se actualiza sola desde sund.es (siempre del origen oficial, como la
de Android): accion=cliente_version dice la versión publicada y el SHA-256 de
cada paquete; accion=cliente&paquete=<linux-x86_64|linux-aarch64|windows-x86_64>
lo descarga. Solo cuando se ejecuta empaquetada (PyInstaller); desde el
código fuente no se toca nada.

Cambio: el paquete se descomprime junto a la carpeta de la app y se
intercambian las carpetas. En Linux se hace al momento y la app se vuelve a
lanzar; en Windows (no se puede mover una carpeta con el .exe en marcha) lo
hace un .bat en cuanto la app se cierra, y vuelve a abrirla.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from . import VERSION, api, plataforma
from .ajustes import ORIGEN_ACTUALIZACIONES


def empaquetada() -> bool:
    return bool(getattr(sys, "frozen", False))


def _tupla(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in v.split("."))


def comprobar() -> dict | None:
    """{"version", "sha256", "tamano"} si hay una versión más nueva para esta máquina."""
    try:
        info = api.get_json(api.url_de(ORIGEN_ACTUALIZACIONES, "cliente_version"), timeout=10)
        paquete = info["paquetes"][plataforma.id_paquete()]
    except (api.ErrorApi, KeyError, TypeError):
        return None
    if _tupla(str(info.get("version", "0"))) <= _tupla(VERSION):
        return None
    return {"version": info["version"], **paquete}


def instalar(nueva: dict, progreso=None) -> None:
    """Descarga, comprueba e instala; luego reinicia la app. No vuelve si va bien."""
    if not empaquetada():
        raise RuntimeError("Solo la app empaquetada se actualiza sola.")
    carpeta_app = Path(sys.executable).resolve().parent
    padre = carpeta_app.parent
    descriptor, temporal = tempfile.mkstemp(dir=padre, suffix=".paquete")
    os.close(descriptor)
    os.remove(temporal)
    try:
        api.descargar(api.url_de(ORIGEN_ACTUALIZACIONES, "cliente", paquete=plataforma.id_paquete()), temporal,
                      progreso=progreso)
        h = hashlib.sha256()
        with open(temporal, "rb") as f:
            while trozo := f.read(1 << 20):
                h.update(trozo)
        if h.hexdigest() != nueva["sha256"]:
            raise api.ErrorApi("El paquete descargado no coincide con el publicado.")
        nueva_carpeta = padre / (carpeta_app.name + ".nueva")
        shutil.rmtree(nueva_carpeta, ignore_errors=True)
        nueva_carpeta.mkdir()
        _extraer(temporal, nueva_carpeta)
        # El paquete trae una carpeta raíz ("romslibrary/"): se sube un nivel.
        dentro = [p for p in nueva_carpeta.iterdir()]
        if len(dentro) == 1 and dentro[0].is_dir():
            for p in dentro[0].iterdir():
                p.rename(nueva_carpeta / p.name)
            dentro[0].rmdir()
    finally:
        if os.path.exists(temporal):
            os.remove(temporal)
    ejecutable = Path(sys.executable).name
    if sys.platform == "win32":
        bat = Path(tempfile.gettempdir()) / "romslibrary-actualizar.bat"
        bat.write_text(
            "@echo off\r\n"
            f":esperar\r\ntasklist /FI \"PID eq {os.getpid()}\" | find \"{os.getpid()}\" >nul && "
            "(timeout /t 1 >nul & goto esperar)\r\n"
            f'rmdir /s /q "{carpeta_app}.anterior" 2>nul\r\n'
            f'move "{carpeta_app}" "{carpeta_app}.anterior"\r\n'
            f'move "{nueva_carpeta}" "{carpeta_app}"\r\n'
            f'start "" "{carpeta_app / ejecutable}"\r\n'
            f'rmdir /s /q "{carpeta_app}.anterior"\r\n', encoding="utf-8")
        subprocess.Popen(["cmd", "/c", str(bat)], creationflags=0x00000008 | 0x00000200)   # DETACHED, NEW_GROUP
        os._exit(0)
    anterior = padre / (carpeta_app.name + ".anterior")
    shutil.rmtree(anterior, ignore_errors=True)
    carpeta_app.rename(anterior)
    nueva_carpeta.rename(carpeta_app)
    shutil.rmtree(anterior, ignore_errors=True)
    os.execv(str(carpeta_app / ejecutable), [str(carpeta_app / ejecutable)] + sys.argv[1:])


def _extraer(paquete: str, destino: Path) -> None:
    raiz = destino.resolve()
    if zipfile.is_zipfile(paquete):
        with zipfile.ZipFile(paquete) as z:
            for m in z.infolist():
                if not (destino / m.filename).resolve().is_relative_to(raiz):
                    raise api.ErrorApi("Paquete con rutas inseguras.")
            z.extractall(destino)
        return
    with tarfile.open(paquete) as t:
        t.extractall(destino, filter="data")
