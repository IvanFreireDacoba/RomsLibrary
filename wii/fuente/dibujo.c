#include "dibujo.h"

#include <SDL2/SDL_image.h>
#include <SDL2/SDL_ttf.h>
#include <stdio.h>
#include <string.h>

#include "plataforma.h"
#include "recursos.h"

int ANCHO = 640;
static SDL_Window *ventana;
static SDL_Renderer *ren;
static TTF_Font *letras[4];

/* Caché de textos ya rasterizados: la Wii no puede rasterizar 30 textos por fotograma. */
#define CACHE 160
static struct {
    char t[160];
    Letra l;
    unsigned color;
    int ancho_max;
    SDL_Texture *tex;
    int w, h;
    unsigned uso;
} cache[CACHE];
static unsigned reloj;

int dibujo_iniciar(int panoramica, char *error, size_t n) {
    if (SDL_Init(SDL_INIT_VIDEO) != 0) {
        snprintf(error, n, "SDL: %s", SDL_GetError());
        return -1;
    }
    ventana = SDL_CreateWindow("RomsLibrary", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, 640, ALTO, 0);
    if (!ventana) {
        snprintf(error, n, "Ventana: %s", SDL_GetError());
        return -1;
    }
    ren = SDL_CreateRenderer(ventana, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    if (!ren) ren = SDL_CreateRenderer(ventana, -1, 0);
    if (!ren) {
        snprintf(error, n, "Renderer: %s", SDL_GetError());
        return -1;
    }
    if (panoramica) ANCHO = 852;                /* se comprime en a_pantalla() */
    SDL_ShowCursor(SDL_DISABLE);                 /* sin el puntero del Wiimote */
    SDL_SetRenderDrawBlendMode(ren, SDL_BLENDMODE_BLEND);
    if (TTF_Init() != 0) {
        snprintf(error, n, "TTF: %s", TTF_GetError());
        return -1;
    }
    IMG_Init(IMG_INIT_JPG | IMG_INIT_PNG);
    static const int tamanos[4] = {14, 17, 20, 26};
    for (int i = 0; i < 4; i++) {
        int negrita = i == LETRA_TITULO;
        SDL_RWops *rw = negrita ? SDL_RWFromConstMem(recurso_fuente_negrita, (int)recurso_fuente_negrita_tam)
                                : SDL_RWFromConstMem(recurso_fuente, (int)recurso_fuente_tam);
        letras[i] = TTF_OpenFontRW(rw, 1, tamanos[i]);
        if (!letras[i]) {
            snprintf(error, n, "Fuente: %s", TTF_GetError());
            return -1;
        }
    }
    return 0;
}

void dibujo_terminar(void) {
    for (int i = 0; i < CACHE; i++)
        if (cache[i].tex) SDL_DestroyTexture(cache[i].tex);
    for (int i = 0; i < 4; i++) TTF_CloseFont(letras[i]);
    TTF_Quit();
    IMG_Quit();
    SDL_DestroyRenderer(ren);
    SDL_DestroyWindow(ventana);
    SDL_Quit();
}

SDL_Renderer *dibujo_renderer(void) { return ren; }

/* Coordenadas lógicas -> píxeles: en 16:9 se comprime en horizontal (no con
 * SDL_RenderSetScale: el renderer de la Wii escala mal las texturas si la escala
 * no es igual en los dos ejes). */
static SDL_Rect a_pantalla(int x, int y, int w, int h) {
    if (ANCHO == 640) return (SDL_Rect){x, y, w, h};
    int px = x * 640 / ANCHO;
    return (SDL_Rect){px, y, (x + w) * 640 / ANCHO - px, h};
}

void imagen(SDL_Texture *t, int x, int y, int w, int h) {
    SDL_Rect d = a_pantalla(x, y, w, h);
    SDL_RenderCopy(ren, t, NULL, &d);
}

static void color_de(unsigned c, int alfa) {
    SDL_SetRenderDrawColor(ren, (c >> 16) & 255, (c >> 8) & 255, c & 255, (Uint8)alfa);
}

void limpiar(unsigned c) {
    color_de(c, 255);
    SDL_RenderClear(ren);
}

void rect(int x, int y, int w, int h, unsigned c) { rect_alfa(x, y, w, h, c, 255); }

void rect_alfa(int x, int y, int w, int h, unsigned c, int alfa) {
    SDL_Rect r = a_pantalla(x, y, w, h);
    color_de(c, alfa);
    SDL_RenderFillRect(ren, &r);
}

void marco(int x, int y, int w, int h, unsigned c) {
    SDL_Rect r = a_pantalla(x, y, w, h);
    color_de(c, 255);
    SDL_RenderDrawRect(ren, &r);
}

int texto_ancho(Letra l, const char *t) {
    int w = 0, h;
    TTF_SizeUTF8(letras[l], t, &w, &h);
    return w;
}

int texto_alto(Letra l) { return TTF_FontHeight(letras[l]); }

/* Recorta `t` (UTF-8) para que quepa en `ancho` con «…». */
static void recortar(Letra l, const char *t, int ancho, char *salida, size_t n) {
    snprintf(salida, n, "%s", t);
    if (ancho <= 0 || texto_ancho(l, salida) <= ancho) return;
    size_t len = strlen(salida);
    while (len > 0) {
        do len--;
        while (len > 0 && ((unsigned char)salida[len] & 0xC0) == 0x80);   /* no partir un carácter */
        salida[len] = 0;
        char prueba[400];
        snprintf(prueba, sizeof prueba, "%s…", salida);
        if (texto_ancho(l, prueba) <= ancho) {
            snprintf(salida, n, "%s", prueba);
            return;
        }
    }
}

static int buscar(Letra l, unsigned color, int ancho, const char *t) {
    for (int i = 0; i < CACHE; i++)
        if (cache[i].tex && cache[i].l == l && cache[i].color == color && cache[i].ancho_max == ancho &&
            !strcmp(cache[i].t, t)) {
            cache[i].uso = ++reloj;
            return i;
        }
    int libre = 0;
    for (int i = 1; i < CACHE; i++)
        if (!cache[i].tex || cache[i].uso < cache[libre].uso) libre = i;
    if (cache[libre].tex) SDL_DestroyTexture(cache[libre].tex);
    cache[libre].tex = NULL;
    char recortado[400];
    recortar(l, t, ancho, recortado, sizeof recortado);
    SDL_Color c = {(color >> 16) & 255, (color >> 8) & 255, color & 255, 255};
    SDL_Surface *s = TTF_RenderUTF8_Blended(letras[l], recortado[0] ? recortado : " ", c);
    if (!s) return -1;
    cache[libre].tex = SDL_CreateTextureFromSurface(ren, s);
    cache[libre].w = s->w;
    cache[libre].h = s->h;
    SDL_FreeSurface(s);
    if (!cache[libre].tex) return -1;
    snprintf(cache[libre].t, sizeof cache[libre].t, "%s", t);
    cache[libre].l = l;
    cache[libre].color = color;
    cache[libre].ancho_max = ancho;
    cache[libre].uso = ++reloj;
    return libre;
}

int texto(int x, int y, Letra l, unsigned color, int ancho, const char *t) {
    if (!t || !*t) return 0;
    if (strlen(t) >= sizeof cache[0].t) {
        /* Demasiado largo para la caché: se dibuja sin guardar. */
        char r[400];
        recortar(l, t, ancho, r, sizeof r);
        SDL_Color c = {(color >> 16) & 255, (color >> 8) & 255, color & 255, 255};
        SDL_Surface *s = TTF_RenderUTF8_Blended(letras[l], r, c);
        if (!s) return 0;
        SDL_Texture *tex = SDL_CreateTextureFromSurface(ren, s);
        imagen(tex, x, y, s->w, s->h);
        int w = s->w;
        SDL_DestroyTexture(tex);
        SDL_FreeSurface(s);
        return w;
    }
    int i = buscar(l, color, ancho, t);
    if (i < 0) return 0;
    imagen(cache[i].tex, x, y, cache[i].w, cache[i].h);
    return cache[i].w;
}

int texto_derecha(int xd, int y, Letra l, unsigned color, const char *t) {
    int w = texto_ancho(l, t);
    return texto(xd - w, y, l, color, 0, t);
}

int parrafo(int x, int y, Letra l, unsigned color, int ancho, const char *t) {
    char linea[400], palabra[200];
    int alto = texto_alto(l), yy = y;
    linea[0] = 0;
    const char *p = t;
    while (*p) {
        size_t lp = strcspn(p, " \n");
        snprintf(palabra, sizeof palabra, "%.*s", (int)lp, p);
        char prueba[600];
        snprintf(prueba, sizeof prueba, "%s%s%s", linea, linea[0] ? " " : "", palabra);
        if (linea[0] && texto_ancho(l, prueba) > ancho) {
            texto(x, yy, l, color, ancho, linea);
            yy += alto;
            snprintf(linea, sizeof linea, "%s", palabra);
        } else {
            snprintf(linea, sizeof linea, "%s", prueba);
        }
        p += lp;
        if (*p == '\n') {
            texto(x, yy, l, color, ancho, linea);
            yy += alto;
            linea[0] = 0;
        }
        if (*p) p++;
    }
    if (linea[0]) {
        texto(x, yy, l, color, ancho, linea);
        yy += alto;
    }
    return yy - y;
}

void presentar(void) {
    plat_fotograma(ren);
    SDL_RenderPresent(ren);
}

SDL_Texture *imagen_cargar(const char *ruta, int w, int h, int *rw, int *rh) {
    SDL_Surface *s = IMG_Load(ruta);
    if (!s) return NULL;
    double ex = (double)w / s->w, ey = (double)h / s->h, e = ex < ey ? ex : ey;
    int dw = (int)(s->w * e), dh = (int)(s->h * e);
    if (dw < 1 || dh < 1) {
        SDL_FreeSurface(s);
        return NULL;
    }
    /* Se escala aquí: la Wii no admite texturas de más de 1024×1024. */
    SDL_Surface *conv = SDL_ConvertSurfaceFormat(s, SDL_PIXELFORMAT_RGBA8888, 0);
    SDL_FreeSurface(s);
    if (!conv) return NULL;
    SDL_Surface *d = SDL_CreateRGBSurfaceWithFormat(0, dw, dh, 32, SDL_PIXELFORMAT_RGBA8888);
    SDL_Texture *t = NULL;
    if (d && SDL_BlitScaled(conv, NULL, d, NULL) == 0) t = SDL_CreateTextureFromSurface(ren, d);
    SDL_FreeSurface(conv);
    SDL_FreeSurface(d);
    *rw = dw;
    *rh = dh;
    return t;
}
