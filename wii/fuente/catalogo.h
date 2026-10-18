/*
 * Catálogo de la librería (accion=juegos, la misma API que la app de
 * Android): solo las consolas que la Wii puede cargar, con los mismos filtros
 * de seguridad, y las versiones en varios idiomas de un juego en una entrada.
 */
#ifndef RL_CATALOGO_H
#define RL_CATALOGO_H

#include "red.h"

#define MAX_IDIOMAS 8

typedef struct {
    char sistema[12];
    char fichero[204];        /* nombre en la librería ("Juego.wbfs", o carpeta si es grupo) */
    char nombre[204];         /* el que se ve: sin extensión */
    long long tamano;
    char portada[600];        /* URL completa o "" */
    int es_grupo;
    char origen_url[300];     /* otra librería pública ("" = la nuestra) */
    char titulo[124];
    char idiomas[MAX_IDIOMAS][3];
    int n_idiomas;
    int corrupto, corrupto_automatico, descargable;
    char motivo_corrupto[304];
    int grupo;                /* mismo número = mismo juego en otro idioma */
    char sha[65];             /* SHA-256 de la versión actual ("" si no se sabe, p. ej. grupos) */
    char fuente_externa[300]; /* descarga en la web del autor ("" si no hay) */
    char sha_enlace[65];      /* SHA-256 de lo que da ese enlace */
    /* local */
    int instalado;
    int actualizable;         /* instalado, y la librería tiene otra versión */
} Juego;

typedef struct {
    Juego *juegos;
    int n;
} Catalogo;

typedef struct {
    const char *id, *nombre;
} Sistema;

extern const Sistema SISTEMAS[];
extern const int N_SISTEMAS;
const char *nombre_sistema(const char *id);

/* Base de la API de una librería ("https://sund.es" -> ".../APIs/RomLibraryApp"). */
void url_api(const char *base, char *salida, size_t n);
/* Base de la librería de la que viene el juego. */
const char *base_de(const Juego *j);
/* La clave solo va a NUESTRA librería. */
const char *clave_de(const Juego *j);

int catalogo_cargar(Conexion *c, Catalogo *cat, char *error, size_t n);
void catalogo_liberar(Catalogo *cat);

/* Índices (en cat->juegos) de una entrada por juego de `sistema`: la variante
 * instalada si la hay; si no, la preferida (sin marca de corrupto, español,
 * inglés...). `solo_instalados` filtra. Devuelve cuántos; *salida hay que liberarla. */
int catalogo_entradas(const Catalogo *cat, const char *sistema, int solo_instalados, int **salida);
/* Variantes (idiomas) del juego `i`, preferidas primero. */
int catalogo_variantes(const Catalogo *cat, int i, int **salida);
void etiqueta_idiomas(const Juego *j, char *salida, size_t n);
void texto_tamano(long long bytes, char *salida, size_t n);

#endif
