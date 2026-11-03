"""
Apartado «Versión»: versión de la app, aviso legal (normas de uso y
privacidad), licencia y licencias de terceros.

No hay texto legal escrito aquí: el aviso legal es la página pública de
sund.es (se descarga y se enseña su texto tal cual; sin conexión, su
dirección), y las licencias son los ficheros de licencia reales que el
empaquetado copia en recursos/licencias (ver empaquetado/construir.sh).
"""

import html.parser
import re
from pathlib import Path

from . import api

URL_AVISO_LEGAL = "https://sund.es/romslibrary/aviso-legal"
CONTACTO = "support@sund.es"
REPOSITORIO = "https://github.com/IvanFreireDacoba/RomsLibrary"

_RECURSOS = Path(__file__).resolve().parent / "recursos"


class _Texto(html.parser.HTMLParser):
    """Texto de la página desde el título (h1) hasta el pie: párrafos,
    títulos y listas, sin menú ni scripts."""

    BLOQUES = {"p", "h1", "h2", "h3", "li", "div", "br", "section"}

    def __init__(self):
        super().__init__()
        self.dentro = False
        self.fuera = False
        self.saltar = 0
        self.partes: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "h1":
            self.dentro = True
        if tag == "footer":
            self.fuera = True
        if tag in ("script", "style", "nav"):
            self.saltar += 1
        if self.dentro and not self.fuera and tag in self.BLOQUES:
            self.partes.append("\n")
            if tag == "li":
                self.partes.append("• ")
            if tag in ("h2", "h3"):
                self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav") and self.saltar:
            self.saltar -= 1

    def handle_data(self, data):
        if self.dentro and not self.fuera and not self.saltar:
            self.partes.append(re.sub(r"\s+", " ", data))

    def texto(self) -> str:
        lineas = [" ".join(linea.split()) for linea in "".join(self.partes).split("\n")]
        salida, vacia = [], False
        for linea in lineas:
            if not linea:
                if not vacia and salida:
                    salida.append("")
                vacia = True
            else:
                salida.append(linea)
                vacia = False
        return "\n".join(salida).strip()


def aviso_legal() -> str:
    """El texto vigente de la página pública. Sin conexión: dónde leerlo."""
    try:
        with api._abrir(URL_AVISO_LEGAL, timeout=15) as r:
            pagina = r.read().decode("utf-8", "replace")
        p = _Texto()
        p.feed(pagina)
        texto = p.texto()
        if len(texto) > 200:
            return f"{texto}\n\n(Texto de {URL_AVISO_LEGAL})"
    except (api.ErrorApi, OSError, ValueError):
        pass
    return (f"No se pudo descargar ahora el aviso legal. Léelo en:\n{URL_AVISO_LEGAL}\n\n"
            f"Contacto: {CONTACTO}")


def licencia() -> str:
    for ruta in (_RECURSOS / "LICENSE", Path(__file__).resolve().parents[2] / "publico" / "LICENSE"):
        if ruta.is_file():
            return _reflujo(ruta.read_text(encoding="utf-8", errors="replace").strip()) + f"\n\nCódigo: {REPOSITORIO}"
    return f"Licencia MIT. Texto completo en {REPOSITORIO}/blob/main/LICENSE"


def _reflujo(texto: str) -> str:
    """Une las líneas cortadas a mano de un fichero de licencia (para que se
    lean seguidas en una pantalla estrecha); respeta párrafos y listas."""
    salida: list[str] = []
    for linea in texto.replace("\r\n", "\n").split("\n"):
        limpia = linea.strip()
        anterior = salida[-1] if salida else ""
        if limpia and anterior and len(anterior) >= 50 and not anterior.endswith(":") \
                and not limpia[:1] in "-*•" and not limpia[:2].rstrip(".)").isdigit():
            salida[-1] = anterior + " " + limpia
        else:
            salida.append(limpia)
    return "\n".join(salida)


def licencias_terceros() -> list[tuple[str, str]]:
    """[(título, texto)] de los ficheros de recursos/licencias (los copia el
    empaquetado de cada componente que va dentro). Vacío desde el código."""
    carpeta = _RECURSOS / "licencias"
    res = []
    indice = carpeta / "INDICE.txt"
    nombres = {}
    if indice.is_file():
        for linea in indice.read_text(encoding="utf-8").splitlines():
            if "\t" in linea:
                fichero, titulo = linea.split("\t", 1)
                nombres[fichero] = titulo
    if carpeta.is_dir():
        for f in sorted(carpeta.iterdir(), key=lambda p: nombres.get(p.name, p.name).lower()):
            if f.is_file() and f.name != "INDICE.txt":
                res.append((nombres.get(f.name, f.name), _reflujo(f.read_text(encoding="utf-8", errors="replace"))))
    return res
