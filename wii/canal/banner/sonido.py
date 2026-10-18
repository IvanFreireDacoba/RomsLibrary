# -*- coding: utf-8 -*-
# Sonido del banner: tres notas suaves (PCM 32 kHz, estéreo, 16 bits). sonido.py salida.raw
import math, struct, sys

TASA = 32000
notas = [(0.00, 1046.5), (0.18, 1318.5), (0.36, 1568.0)]   # do, mi, sol
duracion = 2.2
muestras = []
for n in range(int(TASA * duracion)):
    t = n / float(TASA)
    v = 0.0
    for inicio, f in notas:
        if t >= inicio:
            d = t - inicio
            v += math.sin(2 * math.pi * f * d) * math.exp(-d * 3.2) * min(1.0, d * 200)
    s = int(max(-1.0, min(1.0, v * 0.22)) * 32767)
    muestras.append(struct.pack("<hh", s, s))
open(sys.argv[1], "wb").write(b"".join(muestras))
