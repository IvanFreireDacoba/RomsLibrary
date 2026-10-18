/* Ficheros incrustados en el ejecutable (los genera empaquetado/incrustar.py). */
#ifndef RL_RECURSOS_H
#define RL_RECURSOS_H

#include <stddef.h>

extern const unsigned char recurso_fuente[];          /* DejaVuSans.ttf */
extern const size_t recurso_fuente_tam;
extern const unsigned char recurso_fuente_negrita[];  /* DejaVuSans-Bold.ttf */
extern const size_t recurso_fuente_negrita_tam;
extern const char recurso_cacert[];                   /* certificados raíz de Mozilla (PEM, con '\0') */
extern const size_t recurso_cacert_tam;

#endif
