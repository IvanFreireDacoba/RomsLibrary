"""
Ventanas que se abren encima de la lista: elegir de una lista, confirmar,
teclado en pantalla (para la URL, la clave, el nombre o buscar) y elegir
carpeta. Todas se manejan solo con el mando.

Cada una tiene manejar(accion) -> True si se cierra, y dibujar(sup).
"""

import os
import string
from pathlib import Path

import pygame

from . import tema


class Dialogo:
    titulo = ""

    def manejar(self, accion: str) -> bool:
        return accion == "atras"

    def dibujar(self, sup) -> None:
        pass

    def _marco(self, sup, ancho_rel=0.8, alto_rel=0.8) -> pygame.Rect:
        w, h = sup.get_size()
        velo = pygame.Surface((w, h), pygame.SRCALPHA)
        velo.fill((0, 0, 0, 170))
        sup.blit(velo, (0, 0))
        r = pygame.Rect(0, 0, int(w * ancho_rel), int(h * alto_rel))
        r.center = (w // 2, h // 2)
        tema.caja(sup, r, tema.PANEL, tema.BORDE)
        tema.texto(sup, self.titulo, (r.x + tema.px(20), r.y + tema.px(14)), 28, tema.ACENTO, negrita=True,
                   ancho_max=r.w - tema.px(40))
        return r


class Lista(Dialogo):
    def __init__(self, titulo: str, opciones: list[str], al_elegir, detalle: str = "", inicial: int = 0):
        self.titulo, self.opciones, self.al_elegir, self.detalle = titulo, opciones, al_elegir, detalle
        self.sel = min(inicial, max(0, len(opciones) - 1))

    def manejar(self, accion):
        if accion in ("arriba", "abajo"):
            self.sel = (self.sel + (1 if accion == "abajo" else -1)) % max(1, len(self.opciones))
        elif accion in ("pag_ant", "pag_sig"):
            self.sel = max(0, min(len(self.opciones) - 1, self.sel + (8 if accion == "pag_sig" else -8)))
        elif accion == "aceptar" and self.opciones:
            self.al_elegir(self.sel)
            return True
        return accion == "atras"

    def dibujar(self, sup):
        # Alto según el contenido (como mucho, el 80 % de la pantalla).
        lineas = len(_partir(self.detalle, tema.fuente(20), int(sup.get_width() * 0.8) - tema.px(40))[:4]) \
            if self.detalle else 0
        necesario = tema.px(74) + lineas * tema.px(26) + (tema.px(8) if lineas else 0) + len(self.opciones) * tema.px(44)
        r = self._marco(sup, 0.8, min(0.8, necesario / sup.get_height()))
        y = r.y + tema.px(60)
        if self.detalle:
            for linea in _partir(self.detalle, tema.fuente(20), r.w - tema.px(40))[:4]:
                tema.texto(sup, linea, (r.x + tema.px(20), y), 20, tema.SUAVE)
                y += tema.px(26)
            y += tema.px(8)
        alto = tema.px(44)
        visibles = max(1, (r.bottom - y - tema.px(14)) // alto)
        inicio = max(0, min(self.sel - visibles // 2, len(self.opciones) - visibles))
        for i in range(inicio, min(len(self.opciones), inicio + visibles)):
            fila = pygame.Rect(r.x + tema.px(14), y, r.w - tema.px(28), alto - tema.px(4))
            if i == self.sel:
                tema.caja(sup, fila, tema.SELECCION, tema.ACENTO, 8)
            tema.texto(sup, self.opciones[i], (fila.x + tema.px(12), fila.y + tema.px(8)), 22,
                       ancho_max=fila.w - tema.px(24))
            y += alto


class Confirmar(Lista):
    def __init__(self, titulo: str, texto: str, si: str, al_aceptar, no: str = "Cancelar"):
        super().__init__(titulo, [si, no], lambda i: al_aceptar() if i == 0 else None, detalle=texto)
        self.sel = 1                                    # por defecto, lo seguro


class Mensaje(Lista):
    def __init__(self, titulo: str, texto: str):
        super().__init__(titulo, ["Vale"], lambda i: None, detalle=texto)


def _partir(texto: str, f, ancho: int) -> list[str]:
    lineas = []
    for parrafo in texto.split("\n"):
        actual = ""
        for palabra in parrafo.split(" "):
            prueba = (actual + " " + palabra).strip()
            if f.size(prueba)[0] > ancho and actual:
                lineas.append(actual)
                actual = palabra
            else:
                actual = prueba
        lineas.append(actual)
    return lineas


class Teclado(Dialogo):
    FILAS_MIN = ["1234567890", "qwertyuiop", "asdfghjklñ", "zxcvbnm.-_", ":/@?&=~ "]
    FILAS_MAY = ["1234567890", "QWERTYUIOP", "ASDFGHJKLÑ", "ZXCVBNM,;!", "#%+()'[] "]
    ESPECIALES = ["Mayús", "Borrar", "Espacio", "Aceptar"]

    def __init__(self, titulo: str, inicial: str, al_aceptar, oculto: bool = False):
        self.titulo, self.valor, self.al_aceptar, self.oculto = titulo, inicial, al_aceptar, oculto
        self.mayus = False
        self.f, self.c = 1, 0

    def _filas(self):
        return [list(x) for x in (self.FILAS_MAY if self.mayus else self.FILAS_MIN)] + [self.ESPECIALES]

    def manejar(self, accion):
        filas = self._filas()
        if accion == "arriba":
            self.f = (self.f - 1) % len(filas)
        elif accion == "abajo":
            self.f = (self.f + 1) % len(filas)
        elif accion == "izquierda":
            self.c -= 1
        elif accion == "derecha":
            self.c += 1
        elif accion == "idioma":                        # Y: borrar
            self.valor = self.valor[:-1]
        elif accion == "buscar":                        # X: espacio
            self.valor += " "
        elif accion == "menu":                          # Start: aceptar
            self.al_aceptar(self.valor)
            return True
        elif accion == "filtro":
            self.mayus = not self.mayus
        elif accion == "aceptar":
            tecla = filas[self.f][self.c % len(filas[self.f])]
            if tecla == "Mayús":
                self.mayus = not self.mayus
            elif tecla == "Borrar":
                self.valor = self.valor[:-1]
            elif tecla == "Espacio":
                self.valor += " "
            elif tecla == "Aceptar":
                self.al_aceptar(self.valor)
                return True
            else:
                self.valor += tecla
        self.c %= len(self._filas()[self.f])
        return accion == "atras"

    def escribir(self, texto: str) -> None:
        """Teclado físico (PC)."""
        self.valor += texto

    def dibujar(self, sup):
        r = self._marco(sup, 0.92, 0.86)
        campo = pygame.Rect(r.x + tema.px(20), r.y + tema.px(60), r.w - tema.px(40), tema.px(48))
        tema.caja(sup, campo, tema.FONDO, tema.BORDE, 6)
        mostrado = "•" * len(self.valor) if self.oculto else self.valor
        f = tema.fuente(24)
        while mostrado and f.size(mostrado + "▏")[0] > campo.w - tema.px(20):
            mostrado = mostrado[1:]
        tema.texto(sup, mostrado + "▏", (campo.x + tema.px(10), campo.y + tema.px(10)), 24)
        filas = self._filas()
        y = campo.bottom + tema.px(16)
        alto = (r.bottom - y - tema.px(16)) // len(filas)
        for fi, fila in enumerate(filas):
            ancho = (r.w - tema.px(40)) // len(fila)
            for ci, tecla in enumerate(fila):
                t = pygame.Rect(r.x + tema.px(20) + ci * ancho, y + fi * alto, ancho - tema.px(6), alto - tema.px(6))
                sel = fi == self.f and ci == self.c % len(fila)
                tema.caja(sup, t, tema.SELECCION if sel else tema.FONDO, tema.ACENTO if sel else None, 6)
                tema.texto(sup, "␣" if tecla == " " else tecla, t.center, 22 if len(tecla) == 1 else 18,
                           tema.TEXTO, centrado=True)


class SelectorCarpeta(Lista):
    """Navegar por carpetas: A entra, «Usar esta carpeta» la elige, B cancela."""

    def __init__(self, titulo: str, inicial: str, al_elegir):
        self.ruta = Path(inicial) if inicial and Path(inicial).is_dir() else _raiz_inicial()
        self.al_carpeta = al_elegir
        super().__init__(titulo, [], self._elegido)
        self._cargar()

    def _cargar(self):
        try:
            hijas = sorted((p.name for p in self.ruta.iterdir() if p.is_dir() and not p.name.startswith(".")),
                           key=str.lower)
        except OSError:
            hijas = []
        self.hijas = hijas
        self.opciones = ["✔ Usar esta carpeta", "↑ Subir"] + [f"▸ {h}" for h in hijas]
        self.detalle = str(self.ruta)
        self.sel = 0

    def _elegido(self, i):
        pass

    def manejar(self, accion):
        if accion == "aceptar":
            if self.sel == 0:
                self.al_carpeta(str(self.ruta))
                return True
            if self.sel == 1:
                self.ruta = self.ruta.parent
            else:
                self.ruta = self.ruta / self.hijas[self.sel - 2]
            self._cargar()
            return False
        return super().manejar(accion)


def _raiz_inicial() -> Path:
    for p in ("/mnt", "/userdata", "/storage", str(Path.home())):
        if os.path.isdir(p):
            return Path(p)
    return Path(string.ascii_uppercase[2] + ":/") if os.name == "nt" else Path("/")
