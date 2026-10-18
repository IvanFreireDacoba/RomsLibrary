/*
 * «Instalar como canal» (menú 1): instala RomsLibrary-canal.wad (ver
 * canal/README en wii/README.md) sin gestor de WAD.
 *
 * El WAD lleva firma falsa, como cualquier canal homebrew: solo lo acepta un
 * IOS con el ES parcheado, el cIOS 249 de d2x (ajuste ios_canal, 0 = el IOS
 * actual). Recargar el IOS corta la red, los discos y los mandos, así que
 * después de instalar la app enseña el resultado y vuelve al menú de Wii.
 * Los mandos NO se vuelven a iniciar: WPAD_Init tras WPAD_Shutdown y la
 * recarga del IOS se cuelga dentro de libogc (wiiuse_init_cmd_queue).
 */
#ifndef RL_CANAL_H
#define RL_CANAL_H

#include <stddef.h>

#define CANAL_TITULO 0x00010001524D4C42ULL      /* «RMLB» */

/* Versión instalada del canal (0x100 = 1.0) o -1 si no está instalado. */
int canal_version_instalada(void);
/* 0 si se puede intentar (existe el IOS); si no, el motivo. */
int canal_comprobar(int ios, char *error, size_t n);
/* Instala el WAD (ya descargado y comprobado). 0 si va bien. */
int canal_instalar(const unsigned char *wad, size_t tam, int ios, char *error, size_t n);
/* Después de canal_instalar (sin mandos ni discos): vuelve al menú de Wii. */
void canal_volver_al_menu(void);

#endif
