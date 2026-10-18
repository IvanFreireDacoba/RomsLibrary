/* Dibujo con SDL2: texto (con caché de texturas), rectángulos e imágenes. */
#ifndef RL_DIBUJO_H
#define RL_DIBUJO_H

#include <SDL2/SDL.h>

/* Ancho lógico: 640, o 852 si la Wii está en 16:9 (la imagen de 640 se estira en
 * la tele; se dibuja más ancho y se comprime para que no salga deformado). */
extern int ANCHO;
#define ALTO 480

typedef enum { LETRA_PEQUENA, LETRA_NORMAL, LETRA_GRANDE, LETRA_TITULO } Letra;

/* Colores (los del gestor web: fondo oscuro, verde de acento). */
#define C_FONDO 0x0b0f0c
#define C_PANEL 0x141c17
#define C_BORDE 0x24342a
#define C_TEXTO 0xe6e9ef
#define C_TENUE 0x8a9a90
#define C_ACENTO 0x39ff7a
#define C_NARANJA 0xffa040
#define C_ROJO 0xff5a5a
#define C_AMARILLO 0xffd84a
#define C_AZUL 0x6fb3ff

int dibujo_iniciar(int panoramica, char *error, size_t n);
void dibujo_terminar(void);
SDL_Renderer *dibujo_renderer(void);

void limpiar(unsigned color);
void rect(int x, int y, int w, int h, unsigned color);
void rect_alfa(int x, int y, int w, int h, unsigned color, int alfa);
void marco(int x, int y, int w, int h, unsigned color);
/* Texto en (x, y) (esquina superior izquierda); recorta a `ancho` con «…» si
 * no cabe (ancho <= 0: sin límite). Devuelve el ancho dibujado. */
int texto(int x, int y, Letra l, unsigned color, int ancho, const char *t);
int texto_derecha(int x_derecha, int y, Letra l, unsigned color, const char *t);
int texto_ancho(Letra l, const char *t);
int texto_alto(Letra l);
/* Texto en varias líneas dentro de `ancho`. Devuelve la altura usada. */
int parrafo(int x, int y, Letra l, unsigned color, int ancho, const char *t);
void presentar(void);

/* Dibuja una textura en coordenadas lógicas. */
void imagen(SDL_Texture *t, int x, int y, int w, int h);
/* Imagen escalada para caber en w×h (sin deformar), o NULL. */
SDL_Texture *imagen_cargar(const char *ruta, int w, int h, int *rw, int *rh);

#endif
