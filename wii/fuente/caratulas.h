/*
 * Carátulas: se bajan en un hilo aparte (con su propia conexión) a
 * <carpeta de la app>/caratulas/ y se quedan ahí de caché. Solo se pide la
 * del juego seleccionado; si se cambia de juego antes, gana la última.
 */
#ifndef RL_CARATULAS_H
#define RL_CARATULAS_H

#include <stddef.h>

void caratulas_iniciar(void);
void caratulas_parar(void);
/* 1 si ya está en caché (ruta en `salida`); si no, la pide y devuelve 0. */
int caratula(const char *url, const char *clave, char *salida, size_t n);

#endif
