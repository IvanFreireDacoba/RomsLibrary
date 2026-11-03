#!/usr/bin/env python3
"""nsz de mentira para las pruebas: «comprime» con zlib y apunta cada llamada.
FALSO_NSZ_ROMPER=1 hace que al descomprimir salga un byte distinto."""
import os
import sys
import time
import zlib
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["FALSO_NSZ_LOG"], "a") as log:
    log.write(" ".join(a for a in args if a.startswith("-") and len(a) == 2) + "\n")
salida = Path(args[args.index("-o") + 1]) if "-o" in args else None
entrada = Path(args[-1])
if "-V" in args:
    sys.exit(0)
time.sleep(float(os.environ.get("FALSO_NSZ_PAUSA", "0")))
datos = entrada.read_bytes()
if "-C" in args:
    ext = {".nsp": ".nsz", ".xci": ".xcz"}[entrada.suffix]
    (salida / (entrada.stem + ext)).write_bytes(b"NSZF" + zlib.compress(datos))
elif "-D" in args:
    if datos[:4] != b"NSZF":
        print("no es un nsz"); sys.exit(2)
    plano = zlib.decompress(datos[4:])
    if os.environ.get("FALSO_NSZ_ROMPER") == "1":
        plano = plano[:-1] + bytes([plano[-1] ^ 1])
    ext = {".nsz": ".nsp", ".xcz": ".xci"}[entrada.suffix]
    (salida / (entrada.stem + ext)).write_bytes(plano)
