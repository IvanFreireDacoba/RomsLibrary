#include "guion.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "plataforma.h"

static char guion[4096];
static char *pos;
static uint64_t desde;
static char foto[128], foto_dada[128];
static int terminado;

void guion_iniciar(const char *texto) {
    snprintf(guion, sizeof guion, "%s", texto);
    guion[strcspn(guion, "\r\n")] = 0;
    pos = guion;
    desde = plat_ms();
}

int guion_fin(void) { return terminado; }

const char *guion_foto(void) {
    if (!foto[0]) return NULL;
    snprintf(foto_dada, sizeof foto_dada, "%s", foto);
    foto[0] = 0;
    return foto_dada;
}

unsigned guion_paso(void) {
    if (!pos || !*pos || plat_ms() < desde) return 0;
    char paso[128];
    size_t l = strcspn(pos, ",");
    snprintf(paso, sizeof paso, "%.*s", (int)l, pos);
    pos += l + (pos[l] == ',');
    if (!strncmp(paso, "espera=", 7)) {
        desde = plat_ms() + (uint64_t)atoi(paso + 7);
        return 0;
    }
    if (!strncmp(paso, "foto=", 5)) {
        snprintf(foto, sizeof foto, "%s", paso + 5);
        return 0;
    }
    desde = plat_ms() + 150;
    static const struct { const char *n; unsigned b; } nombres[] = {
        {"arriba", BOTON_ARRIBA}, {"abajo", BOTON_ABAJO}, {"izquierda", BOTON_IZQUIERDA},
        {"derecha", BOTON_DERECHA}, {"a", BOTON_A}, {"b", BOTON_B}, {"menu", BOTON_MENU},
        {"idioma", BOTON_IDIOMA}, {"anterior", BOTON_ANTERIOR}, {"siguiente", BOTON_SIGUIENTE},
        {"salir", BOTON_SALIR}};
    if (!strcmp(paso, "fin")) {
        terminado = 1;
        return 0;
    }
    for (size_t i = 0; i < sizeof nombres / sizeof nombres[0]; i++)
        if (!strcmp(paso, nombres[i].n)) return nombres[i].b;
    fprintf(stderr, "guion: paso desconocido «%s»\n", paso);
    return 0;
}
