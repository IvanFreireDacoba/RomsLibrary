/* En el PC no hay canales: solo para que compile la versión de pruebas. */
#include "canal.h"

#include <stdio.h>
#include <stdlib.h>

int canal_version_instalada(void) { return -1; }

int canal_comprobar(int ios, char *error, size_t n) {
    (void)ios;
    snprintf(error, n, "Solo en la Wii.");
    return -1;
}

int canal_instalar(const unsigned char *wad, size_t tam, int ios, char *error, size_t n) {
    (void)wad;
    (void)tam;
    return canal_comprobar(ios, error, n);
}

void canal_volver_al_menu(void) { exit(0); }
