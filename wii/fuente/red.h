/*
 * Cliente HTTP/1.1 con TLS (mbedTLS), con conexiones que se reutilizan
 * (keep-alive): en la Wii cada negociación TLS cuesta casi un segundo.
 * Una Conexion por hilo; no se comparten.
 */
#ifndef RL_RED_H
#define RL_RED_H

#include <stddef.h>

typedef struct Conexion Conexion;

typedef struct {
    int estado;               /* código HTTP */
    long long longitud;       /* Content-Length, -1 si no viene */
    long long total;          /* tamaño completo (Content-Range) o la longitud */
    int parcial;              /* 206 */
    char ubicacion[2048];     /* Location de una redirección (3xx); las de GitHub pasan de 800 */
} Respuesta;

/* Certificados raíz (una vez, antes de abrir conexiones). 0 si va bien. */
int red_iniciar(char *error, size_t n);

Conexion *con_nueva(void);
void con_liberar(Conexion *c);

/*
 * Envía la petición y lee las cabeceras de la respuesta. `clave` va en
 * X-App-Token (solo a nuestra librería). `desde` > 0 pide Range: bytes=desde-.
 * `cuerpo` (JSON) solo para POST. Devuelve 0 si llegó una respuesta (mira
 * r->estado), -1 si falló la red (error en `error`).
 */
int http_abrir(Conexion *c, const char *metodo, const char *url, const char *clave, long long desde,
               const char *cuerpo, Respuesta *r, char *error, size_t n);
/* Lee del cuerpo: bytes leídos, 0 al terminar, -1 error. */
int http_leer(Conexion *c, void *datos, int n);
/* Hay que llamarla después de cada http_abrir (deja la conexión lista o la cierra). */
void http_cerrar(Conexion *c);

/*
 * Petición completa a memoria (JSON, carátulas). *datos termina en '\0' y hay
 * que liberarlo. Si el estado no es 2xx, en `error` va el «message»/«detail»
 * del JSON de error. Devuelve el código HTTP o -1.
 */
int http_memoria(Conexion *c, const char *metodo, const char *url, const char *clave, const char *cuerpo,
                 char **datos, size_t *n, size_t maximo, char *error, size_t nerror);

/* "a b&c" -> "a%20b%26c" */
void url_codificar(const char *texto, char *salida, size_t n);

#endif
