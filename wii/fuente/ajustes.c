#include "ajustes.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "plataforma.h"

#define MAX_AJUSTES 48

static struct {
    char clave[32];
    char valor[256];
} tabla[MAX_AJUSTES];
static int cuantos;

static const struct {
    const char *clave, *valor;
} defectos[] = {
    {"url", "https://sund.es"},
    {"juegos", "usb"},
    {"roms", "usb"},
};

static void ruta(char *salida, size_t n) { snprintf(salida, n, "%s/ajustes.txt", plat_carpeta_app()); }

void ajustes_cargar(void) {
    cuantos = 0;
    for (size_t i = 0; i < sizeof defectos / sizeof defectos[0]; i++) ajuste_poner(defectos[i].clave, defectos[i].valor);
    char r[300], linea[400];
    ruta(r, sizeof r);
    FILE *f = fopen(r, "r");
    if (!f) return;
    while (fgets(linea, sizeof linea, f)) {
        linea[strcspn(linea, "\r\n")] = 0;
        char *igual = strchr(linea, '=');
        if (!igual || linea[0] == '#') continue;
        *igual = 0;
        ajuste_poner(linea, igual + 1);
    }
    fclose(f);
}

int ajustes_guardar(void) {
    char r[300], tmp[310];
    ruta(r, sizeof r);
    snprintf(tmp, sizeof tmp, "%s.nuevo", r);
    FILE *f = fopen(tmp, "w");
    if (!f) return -1;
    fprintf(f, "# RomsLibrary para Wii (ver README). Se puede editar desde un PC.\n");
    for (int i = 0; i < cuantos; i++) fprintf(f, "%s=%s\n", tabla[i].clave, tabla[i].valor);
    if (fclose(f)) return -1;
    remove(r);                       /* en FAT rename() no sustituye */
    return rename(tmp, r);
}

const char *ajuste(const char *clave) {
    for (int i = 0; i < cuantos; i++)
        if (!strcmp(tabla[i].clave, clave)) return tabla[i].valor;
    return "";
}

void ajuste_poner(const char *clave, const char *valor) {
    int i;
    for (i = 0; i < cuantos; i++)
        if (!strcmp(tabla[i].clave, clave)) break;
    if (i == cuantos) {
        if (cuantos == MAX_AJUSTES) return;
        cuantos++;
        snprintf(tabla[i].clave, sizeof tabla[i].clave, "%s", clave);
    }
    snprintf(tabla[i].valor, sizeof tabla[i].valor, "%s", valor ? valor : "");
}
