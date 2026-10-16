"""
Ajustes y cuenta de esta consola. Se guardan en un JSON en la carpeta de
datos del usuario (~/.config/romslibrary o %APPDATA%\\RomsLibrary); en las
consolas portátiles, junto a la app si la carpeta personal es de solo lectura.

La "credencial" de la consola (lo que en Android es el ANDROID_ID) es un
secreto aleatorio que se genera la primera vez y nunca se enseña: junto con
el identificador público (XXXXX-XXXXX) autoriza a subir y bajar partidas.
"""

import json
import os
import secrets
import sys
from pathlib import Path

from . import plataforma

URL_POR_DEFECTO = "https://sund.es"
ORIGEN_ACTUALIZACIONES = "https://sund.es"


def carpeta_datos() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home()) / "RomsLibrary"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "romslibrary"
    try:
        base.mkdir(parents=True, exist_ok=True)
        (base / ".prueba").write_text("")
        (base / ".prueba").unlink()
        return base
    except OSError:
        local = Path(sys.argv[0]).resolve().parent / "datos"
        local.mkdir(parents=True, exist_ok=True)
        return local


class Ajustes:
    def __init__(self):
        self.ruta = carpeta_datos() / "ajustes.json"
        try:
            self.d = json.loads(self.ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.d = {}
        self.plataforma = plataforma.detectar()
        cambiado = False
        if not self.d.get("credencial"):
            self.d["credencial"] = "pc-" + secrets.token_hex(24)
            cambiado = True
        for clave, valor in (("url", URL_POR_DEFECTO), ("clave", ""), ("roms", self.plataforma.roms),
                             ("partidas", self.plataforma.carpeta_partidas), ("carpetas", {}),
                             ("todas_las_consolas", False), ("identificadores", {}), ("nombres", {}),
                             ("vista", "lista")):
            if clave not in self.d:
                self.d[clave] = valor
                cambiado = True
        if cambiado:
            self.guardar()

    def guardar(self) -> None:
        temporal = self.ruta.with_suffix(".tmp")
        temporal.write_text(json.dumps(self.d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporal, self.ruta)

    def __getitem__(self, k):
        return self.d.get(k)

    def __setitem__(self, k, v):
        self.d[k] = v
        self.guardar()

    # ---------- cuenta en el servidor actual ----------

    @property
    def identificador(self) -> str | None:
        return self.d["identificadores"].get(self.d["url"])

    @property
    def nombre_consola(self) -> str:
        return self.d["nombres"].get(self.d["url"], "")

    def guardar_cuenta(self, identificador: str, nombre: str) -> None:
        self.d["identificadores"][self.d["url"]] = identificador
        self.d["nombres"][self.d["url"]] = nombre
        self.guardar()

    def identidad(self) -> dict:
        return {"android_id": self.d["credencial"], "identificador": self.identificador or ""}

    # ---------- carpetas ----------

    def sistemas_visibles(self) -> list[str]:
        return plataforma.SISTEMAS[:] if self.d["todas_las_consolas"] else self.plataforma.sistemas

    def carpeta_sistema(self, sistema: str) -> Path:
        """Carpeta de una consola dentro de la carpeta ROMs (la de Ajustes si se cambió)."""
        raiz = Path(self.d["roms"])
        propia = self.d["carpetas"].get(sistema)
        if propia:
            return raiz / propia
        alias = self.plataforma.alias.get(sistema)
        if alias:
            existente = plataforma.carpeta_existente(raiz, alias)
            if existente:
                return raiz / existente
        return raiz / self.plataforma.carpetas.get(sistema, sistema)
