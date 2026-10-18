#!/usr/bin/env python3
"""Genera recursos.c (ver fuente/recursos.h): las fuentes y los certificados
raíz van dentro del ejecutable, así la app es un solo boot.dol.

    incrustar.py salida.c DejaVuSans.ttf DejaVuSans-Bold.ttf ca-certificates.crt
"""
import sys


def array(nombre: str, datos: bytes, tipo: str = "unsigned char") -> str:
    filas = ",\n".join(",".join(str(b) for b in datos[i:i + 32]) for i in range(0, len(datos), 32))
    return (f"const {tipo} {nombre}[] = {{\n{filas}\n}};\n"
            f"const size_t {nombre}_tam = {len(datos)};\n")


salida, fuente, negrita, cacert = sys.argv[1:5]
with open(salida, "w") as f:
    f.write('#include "recursos.h"\n\n')
    f.write(array("recurso_fuente", open(fuente, "rb").read()))
    f.write(array("recurso_fuente_negrita", open(negrita, "rb").read()))
    # mbedTLS quiere el PEM terminado en '\0' (y ese byte cuenta en el tamaño).
    f.write(array("recurso_cacert", open(cacert, "rb").read() + b"\0", "char"))
