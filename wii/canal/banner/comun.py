# -*- coding: utf-8 -*-
# Utilidades para mkicono.py y mkbanner.py (Python 2: usan Alameda, del
# Homebrew Channel, que construye_canal.sh descarga en $ALAMEDA).
import os, sys

sys.path.append(os.environ['ALAMEDA'] + "/../Common")
sys.path.append(os.environ['ALAMEDA'])

from Alameda import *


def disposicion(ancho, alto, texturas):
    """Brlyt con una textura y un material (mismo nombre) por fichero."""
    brlyt = Brlyt(None, None, None)
    brlyt.Width = ancho
    brlyt.Height = alto
    for i, t in enumerate(texturas):
        brlyt.Textures.add(Brlyt.BrlytTexture(t + ".tpl"))
        brlyt.Materials.add(Brlyt.BrlytMaterial(t))
        brlyt.Materials[i].Textures.append((i, 0, 0))
        brlyt.Materials[i].TextureCoords.append([0, 0, 0, 1, 1])
        brlyt.Materials[i].SthB.append(0x01041e00)
    brlyt.RootPane = Pane("RootPane")
    brlyt.RootPane.Width = ancho
    brlyt.RootPane.Height = alto
    return brlyt


def imagen(brlyt, nombre, material, x, y, ancho, alto):
    p = Picture(nombre)
    p.Material = material
    p.X, p.Y, p.Width, p.Height = x, y, ancho, alto
    brlyt.RootPane.Add(p)
    return p


def animar(brlan, panel, clase, eje):
    if panel not in brlan.Anim:
        brlan.Anim.add(Brlan.BrlanAnimSet(panel))
    conjunto = brlan.Anim[panel]
    try:
        c = conjunto[clase]
    except KeyError:
        conjunto.add(Brlan.BrlanAnimClass(clase))
        c = conjunto[clase]
    try:
        return c[eje]
    except KeyError:
        c.add(Brlan.BrlanAnim(eje))
        return c[eje]
