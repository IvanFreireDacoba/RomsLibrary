"""
Catálogo de la librería: lectura (con los mismos filtros de seguridad que la
app de Android), versiones en varios idiomas de un mismo juego y qué está
instalado en esta consola.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import api

IDIOMAS = {"es": "Español", "en": "Inglés", "ja": "Japonés", "fr": "Francés", "de": "Alemán", "it": "Italiano",
           "pt": "Portugués"}
_SHA = re.compile(r"[0-9a-f]{64}")


def componente_seguro(nombre) -> bool:
    """Un nombre que acaba en disco tal cual: sin rutas ni trucos."""
    return (isinstance(nombre, str) and 0 < len(nombre) <= 200 and nombre not in (".", "..")
            and not any(c in nombre for c in '/\\:*?"<>|\x00') and not any(ord(c) < 32 for c in nombre)
            and nombre.strip() == nombre and not nombre.endswith("."))


def _enlace_https(url) -> str | None:
    return url if isinstance(url, str) and url.startswith("https://") and "@" not in url and len(url) < 500 else None


@dataclass(eq=False)
class Juego:
    sistema: str
    fichero: str                     # nombre en la librería ("Juego.gba", o carpeta "Juego.cue" si es grupo)
    tamano: int
    portada: str | None = None       # URL completa
    es_grupo: bool = False
    tipo_grupo: str | None = None
    origen: str | None = None        # id de otra librería (None = la nuestra)
    origen_url: str | None = None
    libreria: str = ""
    sha: str | None = None
    fuente_externa: str | None = None
    sha_enlace: str | None = None
    confianza: int = 0
    titulo: str = ""
    idiomas: list[str] = field(default_factory=list)
    corrupto: bool = False
    motivo_corrupto: str = ""
    version: int | None = None
    # Estado local
    instalado: bool = False
    variantes: list["Juego"] = field(default_factory=list)
    progreso: float = -1.0           # 0..1 descargando, -1 nada
    en_cola: bool = False
    error: str = ""

    def base(self, nuestra: str) -> str:
        return self.origen_url or nuestra

    def clave(self, nuestra: str | None) -> str | None:
        return None if self.origen_url else nuestra

    @property
    def nombre_visible(self) -> str:
        return self.fichero

    def etiqueta_idiomas(self) -> str:
        return " ".join(i.upper() for i in self.idiomas)

    def preferencia(self) -> int:
        """Sin marca de corrupto primero; luego español, inglés, otro conocido y desconocido."""
        idioma = 0 if "es" in self.idiomas else 1 if "en" in self.idiomas else 3 if not self.idiomas else 2
        return (10 if self.corrupto else 0) + idioma

    def tamano_texto(self) -> str:
        mb = self.tamano / 1024 / 1024
        return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB" if mb >= 1 else f"{self.tamano // 1024} KB"


def parsear(datos, base: str, sistemas_validos: list[str]) -> list[Juego]:
    lista = []
    for o in datos if isinstance(datos, list) else []:
        if not isinstance(o, dict):
            continue
        sistema, fichero = o.get("system"), o.get("filename")
        if sistema not in sistemas_validos or not componente_seguro(fichero):
            continue
        try:
            tamano = int(o.get("sizeBytes") or 0)
        except (TypeError, ValueError):
            continue
        j = Juego(sistema, fichero, tamano, es_grupo=bool(o.get("esGrupo")),
                  tipo_grupo=o.get("tipoGrupo") if o.get("tipoGrupo") == "switch_base_dlc" else None)
        if o.get("origen"):
            origen_url = _enlace_https(o.get("origenUrl"))
            if not origen_url:
                continue
            j.origen, j.origen_url = str(o["origen"]), origen_url.rstrip("/")
        portada = o.get("coverUrl")
        if isinstance(portada, str):
            if portada.startswith("https://") and j.origen_url and portada.startswith(j.origen_url + "/"):
                j.portada = portada
            elif portada.startswith("/"):
                j.portada = base.rstrip("/") + portada
        j.libreria = str(o.get("libreria") or "")
        sha = o.get("sha")
        j.sha = sha if isinstance(sha, str) and _SHA.fullmatch(sha) else None
        if j.sha:
            j.fuente_externa = _enlace_https(o.get("fuenteExterna"))
            sha_e = o.get("shaEnlace")
            j.sha_enlace = sha_e if isinstance(sha_e, str) and _SHA.fullmatch(sha_e) else j.sha
        j.confianza = int(o.get("confianza") or 0)
        j.titulo = str(o.get("titulo") or "")[:120]
        j.idiomas = [i for i in (o.get("idiomas") or []) if isinstance(i, str) and re.fullmatch(r"[a-z]{2}", i)]
        j.corrupto = o.get("corrupto") is True
        j.motivo_corrupto = str(o.get("motivoCorrupto") or "")[:300]
        j.version = o.get("version") if isinstance(o.get("version"), int) else None
        lista.append(j)
    return lista


def cargar(url: str, clave: str | None, sistemas_validos: list[str], libreria: str = "personal",
           sistema: str | None = None, texto: str | None = None) -> list[Juego]:
    datos = api.get_json(api.url_de(url, "juegos", libreria=libreria, system=sistema, q=texto or None), clave,
                         timeout=40)
    return parsear(datos, url, sistemas_validos)


def agrupar_idiomas(lista: list[Juego]) -> list[Juego]:
    """Una entrada por juego (mismo título, consola y librería): la instalada si
    la hay; si no, la preferida (española, si no inglesa). Ver Juego.preferencia."""
    grupos: dict[tuple, list[Juego]] = {}
    for j in lista:
        clave = (j.origen or "", j.sistema, j.titulo.lower() if j.titulo else "\0" + j.fichero)
        grupos.setdefault(clave, []).append(j)
    resultado = []
    for variantes in grupos.values():
        variantes.sort(key=Juego.preferencia)
        elegido = next((v for v in variantes if v.instalado or v.progreso >= 0 or v.en_cola), variantes[0])
        for v in variantes:
            v.variantes = variantes
        resultado.append(elegido)
    return resultado


def ruta_local(ajustes, j: Juego) -> Path:
    """Dónde queda el juego en esta consola."""
    carpeta = ajustes.carpeta_sistema(j.sistema)
    if j.es_grupo and not ajustes.plataforma.grupos_como_carpeta:
        # Sin ES-DE, "Juego.cue/" no es un juego: va en la carpeta "Juego/".
        return carpeta / Path(j.fichero).stem
    return carpeta / j.fichero


def marcar_instalados(ajustes, lista: list[Juego]) -> None:
    for j in lista:
        r = ruta_local(ajustes, j)
        try:
            j.instalado = r.is_dir() and any(r.iterdir()) if (j.es_grupo or j.sistema == "rpgmaker") \
                else r.is_file() and r.stat().st_size > 0
        except OSError:
            j.instalado = False
