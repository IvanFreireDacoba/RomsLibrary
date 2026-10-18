/*
 * Lo que la app pide a la librería aparte del catálogo:
 *
 * - «Solicitar acceso» (servicio/app/accesos.py): la Wii genera un token
 *   propio y lo manda con su nombre; el administrador lo acepta en el gestor y
 *   ese token pasa a ser la clave de esta Wii en esa librería.
 * - Autoactualización: accion=wii_version dice la versión publicada y el
 *   SHA-256 de cada fichero (boot.dol, meta.xml, icon.png); se bajan junto a
 *   los actuales, se comprueban y solo entonces se cambian. Siempre de sund.es.
 */
#ifndef RL_SERVICIO_H
#define RL_SERVICIO_H

#include <stddef.h>

#include "red.h"

#define ORIGEN_ACTUALIZACIONES "https://sund.es"

int acceso_solicitar(Conexion *c, char *error, size_t n);
/* 1 aceptada (ya es la clave), 0 nada nuevo, -1 rechazada. */
int acceso_comprobar(Conexion *c);

/* 1 si hay versión nueva (en `version`), 0 si no, -1 si no se pudo mirar. */
int actualizacion_buscar(Conexion *c, char *version, size_t n);
/* 0 si quedó instalada (hay que reiniciar la app). */
int actualizacion_instalar(Conexion *c, char *error, size_t n);

/* Versión publicada del canal (0x100 = «1.0»; la deja actualizacion_buscar), -1 si no se sabe. */
extern int canal_version_publicada;
/* Descarga RomsLibrary-canal.wad de sund.es y comprueba su SHA-256 (hay que liberar *wad). */
int canal_descargar(Conexion *c, unsigned char **wad, size_t *tam, char *error, size_t n);

#endif
