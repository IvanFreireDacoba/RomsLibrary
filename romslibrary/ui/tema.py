"""Colores, letras y dibujo básico. Todo se escala con la altura de la
pantalla: igual de legible en 640x480 (RG35XX) que en 1280x800 (Deck)."""

from pathlib import Path

import pygame

FONDO = (14, 17, 22)
PANEL = (24, 29, 37)
SELECCION = (38, 70, 52)
BORDE = (58, 74, 64)
TEXTO = (226, 234, 228)
SUAVE = (150, 166, 158)
ACENTO = (123, 255, 176)
AVISO = (255, 179, 107)
ERROR = (255, 120, 120)
BARRA = (79, 169, 255)

_FUENTES = [Path(__file__).resolve().parent.parent / "recursos" / "DejaVuSans.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("C:/Windows/Fonts/segoeui.ttf")]
_cache: dict[tuple, pygame.font.Font] = {}
escala = 1.0


def configurar(alto: int) -> None:
    global escala
    escala = max(0.6, alto / 720)
    _cache.clear()


def px(valor: float) -> int:
    return max(1, int(valor * escala))


def fuente(tamano: int, negrita: bool = False) -> pygame.font.Font:
    clave = (tamano, negrita)
    if clave not in _cache:
        ruta = next((p for p in _FUENTES if p.is_file()), None)
        f = pygame.font.Font(str(ruta) if ruta else None, px(tamano))
        f.bold = negrita
        _cache[clave] = f
    return _cache[clave]


def texto(sup, cadena: str, pos, tamano: int = 24, color=TEXTO, negrita=False, ancho_max: int | None = None,
          centrado=False) -> pygame.Rect:
    f = fuente(tamano, negrita)
    if ancho_max and f.size(cadena)[0] > ancho_max:
        while cadena and f.size(cadena + "…")[0] > ancho_max:
            cadena = cadena[:-1]
        cadena += "…"
    img = f.render(cadena, True, color)
    r = img.get_rect()
    if centrado:
        r.center = pos
    else:
        r.topleft = pos
    sup.blit(img, r)
    return r


def caja(sup, rect, color=PANEL, borde=None, radio: int = 10) -> None:
    pygame.draw.rect(sup, color, rect, border_radius=px(radio))
    if borde:
        pygame.draw.rect(sup, borde, rect, width=max(1, px(2)), border_radius=px(radio))


def barra(sup, rect, fraccion: float) -> None:
    pygame.draw.rect(sup, (40, 46, 56), rect, border_radius=px(4))
    if fraccion >= 0:
        lleno = pygame.Rect(rect.x, rect.y, int(rect.w * min(1.0, fraccion)), rect.h)
        pygame.draw.rect(sup, BARRA, lleno, border_radius=px(4))


def ayuda(sup, pares: list[tuple[str, str]]) -> None:
    """Pie con los botones: [("A", "Instalar"), ...]."""
    w, h = sup.get_size()
    alto = px(40)
    pygame.draw.rect(sup, PANEL, (0, h - alto, w, alto))
    x = px(14)
    for boton, que in pares:
        r = texto(sup, boton, (x, h - alto + px(8)), 20, ACENTO, negrita=True)
        r2 = texto(sup, que, (r.right + px(6), h - alto + px(8)), 20, SUAVE)
        x = r2.right + px(22)
