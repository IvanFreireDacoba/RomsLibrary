# -*- coding: utf-8 -*-
# Icono del canal (lo que se ve en el menú de Wii): mkicono.py icon.brlyt icon.brlan
import sys
from comun import *

brlyt = disposicion(170, 96, ["fondo", "icono_titulo", "icono_sub"])
imagen(brlyt, "fondo", 0, 0, 0, 170, 96)
imagen(brlyt, "titulo", 1, 0, 8, 120, 34)
imagen(brlyt, "sub", 2, 0, -18, 120, 20)
open(sys.argv[1], "wb").write(brlyt.Pack())

# El título sube y baja muy despacio (16 s por vuelta).
brlan = Brlan()
animar(brlan, "titulo", Brlan.A_COORD, Brlan.C_Y).repsimple(0, 960, 4, 8, 0, 11, 0)
open(sys.argv[2], "wb").write(brlan.Pack(60 * 16))
