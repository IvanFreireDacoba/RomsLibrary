# -*- coding: utf-8 -*-
# Banner del canal (al seleccionarlo): mkbanner.py banner.brlyt banner_Start.brlan banner_Loop.brlan
import sys
from comun import *

brlyt = disposicion(810, 456, ["fondo", "banner_estanteria", "banner_titulo", "banner_sub"])
imagen(brlyt, "fondo", 0, 0, 0, 810, 456)
imagen(brlyt, "estanteria", 1, 0, -130, 640, 160)
imagen(brlyt, "titulo", 2, 0, 60, 600, 140)
imagen(brlyt, "sub", 3, 0, -20, 600, 48)
open(sys.argv[1], "wb").write(brlyt.Pack())

INICIO, FIN = 60, 60 + 960
brlan = Brlan()
# Entrada: el título y el subtítulo aparecen; las carátulas suben desde abajo.
alfa = animar(brlan, "titulo", Brlan.A_PARM, Brlan.P_ALPHA)
alfa.Triplets += [(0, 0, 0), (40, 255, 0), (FIN, 255, 0)]
alfa = animar(brlan, "sub", Brlan.A_PARM, Brlan.P_ALPHA)
alfa.Triplets += [(0, 0, 0), (20, 0, 0), (INICIO, 255, 0), (FIN, 255, 0)]
y = animar(brlan, "estanteria", Brlan.A_COORD, Brlan.C_Y)
y.Triplets += [(0, -230, 0), (INICIO, -130, 0)]
# Bucle: el título flota y las carátulas se mecen.
animar(brlan, "titulo", Brlan.A_COORD, Brlan.C_Y).repsimple(INICIO, FIN, 4, 60, 0, 66, 0)
y.repsimple(INICIO, FIN, 2, -130, 0, -126, 0)
animar(brlan, "estanteria", Brlan.A_COORD, Brlan.C_X).repsimple(INICIO, FIN, 1, -12, 0, 12, 0)
open(sys.argv[2], "wb").write(brlan.Pack(INICIO))
open(sys.argv[3], "wb").write(brlan.Pack(INICIO, FIN))
