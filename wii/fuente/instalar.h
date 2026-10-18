/*
 * Instalar, desinstalar y la cola de descargas (un hilo aparte).
 *
 * Dónde queda cada juego (lo que esperan los cargadores):
 *   Wii       <juegos>/wbfs/<Título> [ID]/<ID>.wbfs (.wbf1, .wbf2... si pasa de 4 GB) o <ID>.iso
 *             (USB Loader GX)
 *   GameCube  <juegos>/games/<Título> [ID]/game.iso                  (Nintendont)
 *   GB/GBC/GBA <roms>/vbagx/roms/                                     (Visual Boy Advance GX)
 *   N64       <roms>/not64/roms/                                      (Not64)
 *   PS1       <roms>/wiisx/isos/                                      (WiiSX)
 * <juegos> y <roms> son «usb:» o «sd:» (ajustes «juegos» y «roms»); la carpeta
 * de cada emulador se cambia con «carpeta_<consola>» en ajustes.txt.
 *
 * En instalados.txt queda, por juego, dónde está y el SHA/tamaño de la versión
 * instalada (para avisar de versiones nuevas).
 *
 * Mientras se descarga, todo va a <unidad>/romslibrary-descargas/ (con
 * «.descargando» al final, que ningún cargador ve) y solo al terminar entero
 * pasa a su sitio. Si la app se cierra a medias, la próxima vez continúa donde
 * lo dejó.
 */
#ifndef RL_INSTALAR_H
#define RL_INSTALAR_H

#include <stddef.h>

#include "catalogo.h"

typedef struct {
    int activa;                      /* hay una descarga en curso */
    char sistema[12], fichero[204], nombre[204];
    long long hecho, total;
    double velocidad;                /* bytes/s */
    int en_cola;                     /* pendientes, sin contar la activa */
    char aviso[400];                 /* último resultado («Instalado ...» o el error) */
    int aviso_es_error;
    unsigned cambios;                /* sube cada vez que algo termina (para refrescar la lista) */
} EstadoDescargas;

void instalar_iniciar(void);
void instalar_parar(void);

/* 1 si la Wii puede usar este juego; si no, 0 y el motivo. */
int instalar_compatible(const Juego *j, char *motivo, size_t n);
int instalar_esta_instalado(const Juego *j);
/* Dónde está instalado ("" si no lo está). */
void instalar_donde(const Juego *j, char *salida, size_t n);
int instalar_en_cola(const Juego *j);
/* -1 si no se está descargando; si no, 0..1. */
double instalar_progreso(const Juego *j);

/* Comprueba sitio y pone el juego en la cola. 0 si va bien; si no, el motivo. */
int instalar_encolar(const Juego *j, char *error, size_t n);
/* La librería tiene otra versión de un juego instalado (otro SHA, o otro tamaño
 * si no hay SHA). Lo instalado antes de guardar el SHA se da por actual. */
int instalar_hay_version_nueva(const Juego *j);
/* Baja la versión nueva y, cuando está entera, sustituye a la instalada. */
int instalar_actualizar(const Juego *j, char *error, size_t n);
/* Quita de la cola o para la descarga en curso (y borra lo descargado). */
void instalar_cancelar(const Juego *j);
int instalar_borrar(const Juego *j, char *error, size_t n);
void instalar_estado(EstadoDescargas *e);
/* Descargas a medias de otras veces (bytes) y borrarlas. */
long long instalar_restos(void);
void instalar_borrar_restos(void);

/* La raíz de juegos/roms para un sistema ("usb:"), o NULL si esa unidad no está. */
const char *instalar_raiz(const char *sistema);

/* Copia segura de un nombre para FAT32. */
void nombre_fat(const char *texto, char *salida, size_t n);
int borrar_recursivo(const char *ruta);

#endif
