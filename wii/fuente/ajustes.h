/*
 * Ajustes en <carpeta de la app>/ajustes.txt, «clave=valor» por línea, para
 * poder cambiarlos también desde un PC:
 *
 *   url=https://sund.es          librería a la que se conecta
 *   clave=                       clave de acceso (o el token de una consola aceptada)
 *   acceso_pendiente=            token de una solicitud de acceso sin responder
 *   juegos=usb                   dónde van los juegos de Wii y GameCube (usb|sd)
 *   roms=usb                     dónde van los de emuladores (usb|sd)
 *   carpeta_gba=/vbagx/roms      carpeta de cada consola de emulador (ver instalar.c)
 */
#ifndef RL_AJUSTES_H
#define RL_AJUSTES_H

void ajustes_cargar(void);
int ajustes_guardar(void);
const char *ajuste(const char *clave);            /* "" si no está */
void ajuste_poner(const char *clave, const char *valor);

#endif
