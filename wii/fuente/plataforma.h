/*
 * Lo que cambia entre la Wii (libogc) y Linux (para probar la app en un PC):
 * red, discos, mandos, entropía y reloj. Todo lo demás es común.
 */
#ifndef RL_PLATAFORMA_H
#define RL_PLATAFORMA_H

#include <stddef.h>
#include <stdint.h>

/* Botones ya traducidos (Wiimote, mando Clásico, mando de GameCube o teclado). */
enum {
    BOTON_ARRIBA = 1 << 0,
    BOTON_ABAJO = 1 << 1,
    BOTON_IZQUIERDA = 1 << 2,
    BOTON_DERECHA = 1 << 3,
    BOTON_A = 1 << 4,          /* aceptar */
    BOTON_B = 1 << 5,          /* volver / cancelar */
    BOTON_MENU = 1 << 6,       /* Wiimote 1 · Clásico X · GameCube Start */
    BOTON_IDIOMA = 1 << 7,     /* Wiimote 2 · Clásico Y · GameCube Y */
    BOTON_ANTERIOR = 1 << 8,   /* Wiimote − · Clásico/GameCube L */
    BOTON_SIGUIENTE = 1 << 9,  /* Wiimote + · Clásico/GameCube R */
    BOTON_SALIR = 1 << 10,     /* HOME · GameCube Z */
};

int plat_iniciar(int argc, char **argv);
void plat_terminar(void);
/* La consola pide apagarse o reiniciarse (botón POWER/RESET): hay que salir ya. */
int plat_debe_salir(void);

/* La consola está configurada en 16:9. */
int plat_panoramica(void);
/* Monta el USB si aún no lo está (algunos discos tardan en despertar). */
void plat_esperar_usb(void);
/* NULL si el USB está montado; si no, por qué (sin disco, NTFS, exFAT, GPT...). */
const char *plat_problema_usb(void);

/* Carpeta de la app (donde está boot.dol): ajustes, caché de carátulas. Sin '/' final. */
const char *plat_carpeta_app(void);
/* "usb:" / "sd:" si están montados, si no NULL. */
const char *plat_raiz(const char *dispositivo);
/* Espacio libre en bytes de una raíz ("usb:"), -1 si no se sabe. */
long long plat_libre(const char *raiz);

int plat_red_iniciar(char *error, size_t n);
/* Socket TCP conectado o -1 (error en `error`). */
int plat_conectar(const char *host, int puerto, char *error, size_t n);
int plat_enviar(int s, const void *datos, int n);
/* Bytes recibidos, 0 si se cerró, -1 error, -2 tiempo agotado. */
int plat_recibir(int s, void *datos, int n, int espera_ms);
void plat_cerrar(int s);

/* Bytes aleatorios para TLS y para los tokens de acceso. */
void plat_entropia(unsigned char *salida, size_t n);
uint64_t plat_ms(void);
void plat_dormir_ms(int ms);

/* Nombre de la consola («Nombre de la consola» de la Wii) para la solicitud de acceso. */
void plat_nombre_consola(char *salida, size_t n);

/* Botones pulsados desde la última llamada y los que siguen mantenidos. */
void plat_leer_botones(unsigned *pulsados, unsigned *mantenidos);

/* Solo Linux (pruebas): guardar lo que se ve en pantalla. Ver plataforma_linux.c. */
struct SDL_Renderer;
void plat_fotograma(struct SDL_Renderer *r);

#endif
