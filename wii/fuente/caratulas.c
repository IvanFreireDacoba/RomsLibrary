#include "caratulas.h"

#include <SDL2/SDL.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "plataforma.h"
#include "red.h"

static SDL_mutex *cerrojo;
static SDL_cond *aviso;
static SDL_Thread *hilo;
static volatile int parar;
static char pedida_url[600], pedida_clave[100], en_curso[600];
static char fallidas[32][600];         /* no se vuelven a pedir en esta sesión */
static int n_fallidas;

static void ruta_de(const char *url, char *salida, size_t n) {
    unsigned long long h = 1469598103934665603ULL;
    for (const char *p = url; *p; p++) h = (h ^ (unsigned char)*p) * 1099511628211ULL;
    snprintf(salida, n, "%s/caratulas/%016llx.img", plat_carpeta_app(), h);
}

static int bucle(void *nada) {
    (void)nada;
    Conexion *c = con_nueva();
    char url[600], clave[100], ruta[400], tmp[420], error[200];
    char carpeta[300];
    snprintf(carpeta, sizeof carpeta, "%s/caratulas", plat_carpeta_app());
    mkdir(carpeta, 0777);
    SDL_LockMutex(cerrojo);
    while (!parar) {
        if (!pedida_url[0]) {
            SDL_CondWaitTimeout(aviso, cerrojo, 500);
            continue;
        }
        snprintf(url, sizeof url, "%s", pedida_url);
        snprintf(clave, sizeof clave, "%s", pedida_clave);
        pedida_url[0] = 0;
        snprintf(en_curso, sizeof en_curso, "%s", url);
        SDL_UnlockMutex(cerrojo);

        ruta_de(url, ruta, sizeof ruta);
        snprintf(tmp, sizeof tmp, "%s.parcial", ruta);
        char *datos;
        size_t l;
        int ok = 0;
        if (http_memoria(c, "GET", url, clave[0] ? clave : NULL, NULL, &datos, &l, 8u << 20, error, sizeof error) == 200 && l > 0) {
            FILE *f = fopen(tmp, "wb");
            ok = f && fwrite(datos, 1, l, f) == l;
            if (f && fclose(f)) ok = 0;
            if (ok) {
                remove(ruta);
                ok = rename(tmp, ruta) == 0;
            }
            if (!ok) remove(tmp);
        }
        free(datos);

        SDL_LockMutex(cerrojo);
        en_curso[0] = 0;
        if (!ok && n_fallidas < 32) snprintf(fallidas[n_fallidas++], sizeof fallidas[0], "%s", url);
    }
    SDL_UnlockMutex(cerrojo);
    con_liberar(c);
    return 0;
}

void caratulas_iniciar(void) {
    cerrojo = SDL_CreateMutex();
    aviso = SDL_CreateCond();
    hilo = SDL_CreateThread(bucle, "caratulas", NULL);
}

void caratulas_parar(void) {
    if (!hilo) return;
    parar = 1;
    SDL_CondSignal(aviso);
    SDL_WaitThread(hilo, NULL);
    hilo = NULL;
}

int caratula(const char *url, const char *clave, char *salida, size_t n) {
    struct stat s;
    ruta_de(url, salida, n);
    if (stat(salida, &s) == 0 && s.st_size > 0) return 1;
    SDL_LockMutex(cerrojo);
    int fallida = 0;
    for (int i = 0; i < n_fallidas; i++) fallida |= !strcmp(fallidas[i], url);
    if (!fallida && strcmp(pedida_url, url) && strcmp(en_curso, url)) {
        snprintf(pedida_url, sizeof pedida_url, "%s", url);
        snprintf(pedida_clave, sizeof pedida_clave, "%s", clave ? clave : "");
        SDL_CondSignal(aviso);
    }
    SDL_UnlockMutex(cerrojo);
    return 0;
}
