/*
 * Pruebas automáticas (no se usa en el día a día): una lista de pasos
 * separados por comas que pulsa botones sin mando.
 *   espera=ms   pausa         foto=nombre   captura (solo en Linux)
 *   fin         salir         arriba, abajo, izquierda, derecha, a, b, menu,
 *                             idioma, anterior, siguiente, salir: ese botón
 * En Linux viene de RL_TECLAS; en la Wii, de guion-pruebas.txt junto a boot.dol.
 */
#ifndef RL_GUION_H
#define RL_GUION_H

void guion_iniciar(const char *texto);
/* Botones del paso actual (0 si toca esperar). */
unsigned guion_paso(void);
/* Nombre de la foto pendiente (y la da por hecha), o NULL. */
const char *guion_foto(void);
int guion_fin(void);

#endif
