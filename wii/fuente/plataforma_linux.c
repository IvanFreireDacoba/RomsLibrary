/*
 * Linux, solo para probar la app en un PC (no se publica). Las «unidades»
 * son carpetas: RL_APP (la de la app), RL_USB y RL_SD.
 *
 * Teclado: flechas, Intro (A), Retroceso/Esc (B), M (menú), I (idioma),
 * Re Pág/Av Pág o Q/E (consola anterior/siguiente), F10 (salir).
 *
 * Pruebas automáticas: RL_TECLAS="espera=4000,abajo,a,foto=lista,fin" (ver
 * guion.c); las fotos van a RL_FOTOS/nombre.bmp.
 */
#include "plataforma.h"
#include "guion.h"

#include <SDL2/SDL.h>
#include <arpa/inet.h>
#include <errno.h>
#include <netdb.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <sys/statvfs.h>
#include <time.h>
#include <unistd.h>

static int salir;

static const char *entorno(const char *nombre, const char *defecto) {
    const char *v = getenv(nombre);
    return v && *v ? v : defecto;
}

int plat_iniciar(int argc, char **argv) {
    (void)argc;
    (void)argv;
    const char *g = getenv("RL_TECLAS");
    if (g) guion_iniciar(g);
    return 0;
}

void plat_terminar(void) {}
void plat_esperar_usb(void) {}
const char *plat_problema_usb(void) { return getenv("RL_USB") ? NULL : "No hay ningún disco USB conectado."; }
int plat_panoramica(void) { return getenv("RL_PANORAMICA") != NULL; }
int plat_debe_salir(void) { return salir || guion_fin(); }
const char *plat_carpeta_app(void) { return entorno("RL_APP", "/tmp/rl-wii/sd/apps/romslibrary"); }

const char *plat_raiz(const char *dispositivo) {
    if (!strcmp(dispositivo, "usb")) return getenv("RL_USB");
    if (!strcmp(dispositivo, "sd")) return getenv("RL_SD");
    return NULL;
}

long long plat_libre(const char *raiz) {
    struct statvfs s;
    if (statvfs(raiz, &s) != 0) return -1;
    return (long long)s.f_bavail * (long long)s.f_frsize;
}

int plat_red_iniciar(char *error, size_t n) {
    (void)error;
    (void)n;
    return 0;
}

int plat_conectar(const char *host, int puerto, char *error, size_t n) {
    struct addrinfo pista = {0}, *res = NULL;
    char p[8];
    pista.ai_family = AF_INET;
    pista.ai_socktype = SOCK_STREAM;
    snprintf(p, sizeof p, "%d", puerto);
    if (getaddrinfo(host, p, &pista, &res) != 0 || !res) {
        snprintf(error, n, "No se encuentra %s (DNS).", host);
        return -1;
    }
    int s = socket(res->ai_family, res->ai_socktype, 0);
    if (s < 0 || connect(s, res->ai_addr, res->ai_addrlen) != 0) {
        snprintf(error, n, "No se pudo conectar con %s (%s).", host, strerror(errno));
        if (s >= 0) close(s);
        freeaddrinfo(res);
        return -1;
    }
    freeaddrinfo(res);
    return s;
}

int plat_enviar(int s, const void *datos, int n) {
    ssize_t r = send(s, datos, n, MSG_NOSIGNAL);
    return r < 0 ? -1 : (int)r;
}

int plat_recibir(int s, void *datos, int n, int espera_ms) {
    struct pollfd p = {.fd = s, .events = POLLIN};
    int r = poll(&p, 1, espera_ms);
    if (r == 0) return -2;
    if (r < 0) return -1;
    ssize_t l = recv(s, datos, n, 0);
    return l < 0 ? -1 : (int)l;
}

void plat_cerrar(int s) {
    if (s >= 0) close(s);
}

void plat_entropia(unsigned char *salida, size_t n) {
    while (n) {
        ssize_t r = getrandom(salida, n, 0);
        if (r <= 0) abort();
        salida += r;
        n -= (size_t)r;
    }
}

uint64_t plat_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000 + (uint64_t)t.tv_nsec / 1000000;
}

void plat_dormir_ms(int ms) { usleep(ms * 1000); }

void plat_nombre_consola(char *salida, size_t n) { snprintf(salida, n, "%s", entorno("RL_NOMBRE", "Wii de pruebas")); }

static unsigned tecla(SDL_Keycode k) {
    switch (k) {
    case SDLK_UP: return BOTON_ARRIBA;
    case SDLK_DOWN: return BOTON_ABAJO;
    case SDLK_LEFT: return BOTON_IZQUIERDA;
    case SDLK_RIGHT: return BOTON_DERECHA;
    case SDLK_RETURN: return BOTON_A;
    case SDLK_BACKSPACE: case SDLK_ESCAPE: return BOTON_B;
    case SDLK_m: return BOTON_MENU;
    case SDLK_i: return BOTON_IDIOMA;
    case SDLK_PAGEUP: case SDLK_q: return BOTON_ANTERIOR;
    case SDLK_PAGEDOWN: case SDLK_e: return BOTON_SIGUIENTE;
    case SDLK_F10: return BOTON_SALIR;
    default: return 0;
    }
}

void plat_leer_botones(unsigned *pulsados, unsigned *mantenidos) {
    unsigned p = 0;
    SDL_Event e;
    while (SDL_PollEvent(&e)) {
        if (e.type == SDL_QUIT) salir = 1;
        if (e.type == SDL_KEYDOWN && !e.key.repeat) p |= tecla(e.key.keysym.sym);
    }
    unsigned m = 0;
    const Uint8 *estado = SDL_GetKeyboardState(NULL);
    static const SDL_Scancode codigos[] = {SDL_SCANCODE_UP, SDL_SCANCODE_DOWN};
    if (estado[codigos[0]]) m |= BOTON_ARRIBA;
    if (estado[codigos[1]]) m |= BOTON_ABAJO;
    p |= guion_paso();
    SDL_Delay(15);                  /* sin vsync en pruebas: no gastar un núcleo entero */
    *pulsados = p;
    *mantenidos = m | p;
}

void plat_fotograma(struct SDL_Renderer *r) {
    const char *foto_pendiente = guion_foto();
    if (!foto_pendiente) return;
    SDL_Surface *s = SDL_CreateRGBSurfaceWithFormat(0, 640, 480, 32, SDL_PIXELFORMAT_ARGB8888);
    if (s && SDL_RenderReadPixels(r, NULL, SDL_PIXELFORMAT_ARGB8888, s->pixels, s->pitch) == 0) {
        char ruta[512];
        snprintf(ruta, sizeof ruta, "%s/%s.bmp", entorno("RL_FOTOS", "/tmp"), foto_pendiente);
        SDL_SaveBMP(s, ruta);
        fprintf(stderr, "foto: %s\n", ruta);
    }
    SDL_FreeSurface(s);
}
