/*
 * RomsLibrary para Wii (Homebrew Channel): la librería de RomsLibrary con el
 * mando. Instala juegos de Wii y GameCube en el disco de USB Loader GX y de
 * consolas antiguas en las carpetas de sus emuladores; también los borra.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ajustes.h"
#include "caratulas.h"
#include "canal.h"
#include "catalogo.h"
#include "dibujo.h"
#include "instalar.h"
#include "plataforma.h"
#include "red.h"
#include "servicio.h"
#include "version.h"

#define FILAS 12
#define ALTO_FILA 27
#define LISTA_Y 92
#define LISTA_ANCHO (ANCHO - 236)

typedef enum { M_LISTA, M_MENU, M_IDIOMAS, M_CONFIRMAR, M_AVISO, M_ACERCA, M_OPCIONES } Modo;
typedef enum { C_NADA, C_INSTALAR_CORRUPTO, C_BORRAR, C_SALIR, C_ACTUALIZAR, C_BORRAR_RESTOS, C_ACTUALIZAR_JUEGOS, C_CANAL } Confirmacion;

static Catalogo cat;
static Conexion *con;
static int pestanas[16], n_pestanas, pestana;
static int *entradas, n_entradas, sel, primera;
static int solo_instalados;
static int *preferida;                 /* por grupo: variante elegida con «Idioma» (-1 = la de siempre) */
static Modo modo = M_LISTA;
static Confirmacion confirmar;
static int confirmar_juego = -1;
static char mensaje[600], titulo_mensaje[100];
static char aviso_texto[400];
static unsigned aviso_color;
static uint64_t aviso_hasta;
static int menu_sel, idioma_sel, opcion_sel, n_opciones;
static int opciones[3];                /* 0 actualizar, 1 borrar, 2 volver */
static int *variantes, n_variantes;
static int error_catalogo;             /* 401: sin acceso */
static char error_catalogo_texto[300];
static unsigned cambios_vistos;
static long long libre = -1;
static uint64_t libre_cuando;
static char version_nueva[32];
static int salir;
static const char *problema_usb;      /* se mira una vez al arrancar (lee el disco) */

/* carátula del seleccionado */
static SDL_Texture *portada;
static int portada_w, portada_h, portada_de = -1;
static uint64_t portada_desde;

static void avisar(const char *t, unsigned color) {
    snprintf(aviso_texto, sizeof aviso_texto, "%s", t);
    aviso_color = color;
    aviso_hasta = plat_ms() + 6000;
}

static void dialogo(const char *titulo, const char *t) {
    snprintf(titulo_mensaje, sizeof titulo_mensaje, "%s", titulo);
    snprintf(mensaje, sizeof mensaje, "%s", t);
    modo = M_AVISO;
}

static void pantalla_espera(const char *t) {
    limpiar(C_FONDO);
    texto(40, 40, LETRA_TITULO, C_ACENTO, 0, "RomsLibrary");
    texto(40, 200, LETRA_GRANDE, C_TEXTO, 560, t);
    presentar();
}

static const Juego *juego_sel(void) { return n_entradas ? &cat.juegos[entradas[sel]] : NULL; }

static void soltar_portada(void) {
    if (portada) SDL_DestroyTexture(portada);
    portada = NULL;
    portada_de = -1;
}

/* Las pestañas: las consolas con algún juego (todas si no hay ninguno). */
static void calcular_pestanas(void) {
    const char *antes = n_pestanas ? SISTEMAS[pestanas[pestana]].id : "wii";
    n_pestanas = 0;
    for (int s = 0; s < N_SISTEMAS; s++) {
        int hay = 0;
        for (int i = 0; i < cat.n && !hay; i++) hay = !strcmp(cat.juegos[i].sistema, SISTEMAS[s].id);
        if (hay) pestanas[n_pestanas++] = s;
    }
    if (!n_pestanas) pestanas[n_pestanas++] = 0;
    pestana = 0;
    for (int i = 0; i < n_pestanas; i++)
        if (!strcmp(SISTEMAS[pestanas[i]].id, antes)) pestana = i;
}

static void calcular_entradas(void) {
    const Juego *antes = juego_sel();
    char sis[12] = "", fich[204] = "";
    if (antes) {
        snprintf(sis, sizeof sis, "%s", antes->sistema);
        snprintf(fich, sizeof fich, "%s", antes->fichero);
    }
    free(entradas);
    n_entradas = catalogo_entradas(&cat, SISTEMAS[pestanas[pestana]].id, solo_instalados, &entradas);
    /* La variante elegida con «Idioma» gana a la de siempre. */
    for (int i = 0; i < n_entradas; i++) {
        int g = cat.juegos[entradas[i]].grupo;
        if (preferida && preferida[g] >= 0 && !cat.juegos[entradas[i]].instalado && !instalar_en_cola(&cat.juegos[entradas[i]]))
            entradas[i] = preferida[g];
    }
    if (sel >= n_entradas) sel = n_entradas ? n_entradas - 1 : 0;
    for (int i = 0; i < n_entradas; i++) {
        const Juego *j = &cat.juegos[entradas[i]];
        if (cat.juegos[entradas[i]].grupo == (antes ? antes->grupo : -1) && !strcmp(j->sistema, sis)) sel = i;
    }
    (void)fich;
    if (sel < primera) primera = sel;
    if (sel >= primera + FILAS) primera = sel - FILAS + 1;
    if (primera < 0) primera = 0;
    soltar_portada();
}

static void refrescar_instalados(void) {
    for (int i = 0; i < cat.n; i++) {
        cat.juegos[i].instalado = instalar_esta_instalado(&cat.juegos[i]);
        cat.juegos[i].actualizable = cat.juegos[i].instalado && instalar_hay_version_nueva(&cat.juegos[i]);
    }
    calcular_entradas();
    libre_cuando = 0;
}

static void cargar_catalogo(void) {
    pantalla_espera("Cargando la librería…");
    catalogo_liberar(&cat);
    free(preferida);
    preferida = NULL;
    char error[300] = "";
    int r = catalogo_cargar(con, &cat, error, sizeof error);
    error_catalogo = r < 0 ? -r : 0;
    snprintf(error_catalogo_texto, sizeof error_catalogo_texto, "%s", r ? error : "");
    if (r == -401) {
        snprintf(error_catalogo_texto, sizeof error_catalogo_texto,
                 *ajuste("acceso_pendiente") ? "Has pedido acceso a esta librería: falta que lo acepte su administrador."
                                            : "Esta librería es privada. Pulsa 1 → «Solicitar acceso» y acéptalo en su gestor.");
    }
    preferida = malloc(sizeof(int) * (size_t)(cat.n + 1));
    for (int i = 0; preferida && i <= cat.n; i++) preferida[i] = -1;
    calcular_pestanas();
    sel = primera = 0;
    calcular_entradas();
}

static int juegos_con_version_nueva(void) {
    int n = 0;
    for (int i = 0; i < cat.n; i++) n += cat.juegos[i].actualizable && !instalar_en_cola(&cat.juegos[i]);
    return n;
}

static void actualizar_todos(void) {
    char e[300];
    int n = 0, fallos = 0;
    for (int i = 0; i < cat.n; i++)
        if (cat.juegos[i].actualizable && !instalar_en_cola(&cat.juegos[i])) {
            if (instalar_actualizar(&cat.juegos[i], e, sizeof e)) fallos++;
            else n++;
        }
    char t[300];
    if (fallos) snprintf(t, sizeof t, "%d en cola; %d no: %s", n, fallos, e);
    else snprintf(t, sizeof t, "%d %s en cola.", n, n == 1 ? "actualización" : "actualizaciones");
    avisar(t, fallos ? C_NARANJA : C_ACENTO);
}

/* ---------------- acciones ---------------- */

static void accion_a(void) {
    const Juego *j = juego_sel();
    if (!j) return;
    char motivo[300];
    if (instalar_en_cola(j)) {
        instalar_cancelar(j);
        avisar(instalar_progreso(j) >= 0 ? "Parando la descarga…" : "Quitado de la cola.", C_TENUE);
        return;
    }
    if (j->instalado) {
        n_opciones = 0;
        if (j->actualizable) opciones[n_opciones++] = 0;
        opciones[n_opciones++] = 1;
        opciones[n_opciones++] = 2;
        opcion_sel = 0;
        confirmar_juego = entradas[sel];
        modo = M_OPCIONES;
        return;
    }
    if (!instalar_compatible(j, motivo, sizeof motivo)) {
        avisar(motivo, C_NARANJA);
        return;
    }
    if (!j->descargable) {
        avisar("Confirmado como corrupto y sin versión buena: la librería no lo sirve.", C_ROJO);
        return;
    }
    if (j->corrupto) {
        confirmar = C_INSTALAR_CORRUPTO;
        confirmar_juego = entradas[sel];
        modo = M_CONFIRMAR;
        return;
    }
    if (instalar_encolar(j, motivo, sizeof motivo)) avisar(motivo, C_ROJO);
    else avisar("Añadido a las descargas.", C_ACENTO);
}

static void confirmar_si(void) {
    const Juego *j = confirmar_juego >= 0 ? &cat.juegos[confirmar_juego] : NULL;
    char e[300];
    modo = M_LISTA;
    switch (confirmar) {
    case C_INSTALAR_CORRUPTO:
        if (instalar_encolar(j, e, sizeof e)) avisar(e, C_ROJO);
        else avisar("Añadido a las descargas.", C_ACENTO);
        break;
    case C_BORRAR:
        if (instalar_borrar(j, e, sizeof e)) avisar(e, C_ROJO);
        else {
            avisar("Borrado del disco.", C_ACENTO);
            refrescar_instalados();
        }
        break;
    case C_SALIR:
        salir = 1;
        break;
    case C_ACTUALIZAR:
        pantalla_espera("Actualizando RomsLibrary…");
        if (actualizacion_instalar(con, e, sizeof e)) dialogo("No se pudo actualizar", e);
        else {
            dialogo("RomsLibrary actualizada",
                    "Ya está instalada la versión nueva. Pulsa A para volver al Homebrew Channel y ábrela otra vez.");
            confirmar = C_SALIR;
        }
        break;
    case C_ACTUALIZAR_JUEGOS:
        actualizar_todos();
        break;
    case C_CANAL: {
        int ios = *ajuste("ios_canal") ? atoi(ajuste("ios_canal")) : 249;
        unsigned char *wad;
        size_t tam;
        pantalla_espera("Descargando el canal…");
        if (canal_descargar(con, &wad, &tam, e, sizeof e)) {
            dialogo("No se pudo descargar el canal", e);
            break;
        }
        /* Recargar el IOS corta la red y los discos: antes se paran los hilos. */
        pantalla_espera("Instalando el canal… no apagues la Wii.");
        caratulas_parar();
        instalar_parar();
        con_liberar(con);
        con = NULL;
        int r = canal_instalar(wad, tam, ios, e, sizeof e);
        free(wad);
        if (r) dialogo("No se pudo instalar el canal", e);
        else dialogo("Canal instalado",
                     "RomsLibrary ya está en el menú de Wii. El canal abre apps/romslibrary de la SD (o del USB): no la "
                     "borres. Pulsa A para cerrar la app.");
        confirmar = C_SALIR;
        break;
    }
    case C_BORRAR_RESTOS:
        instalar_borrar_restos();
        avisar("Descargas sin terminar borradas.", C_ACENTO);
        libre_cuando = 0;
        break;
    default:
        break;
    }
}

/* ---------------- menú ---------------- */

typedef enum { MI_ACCESO, MI_ACTUALIZAR_JUEGOS, MI_INSTALADOS, MI_RECARGAR, MI_BUSCAR_APP, MI_CANAL, MI_RESTOS, MI_ACERCA, MI_SALIR } ItemMenu;
static ItemMenu items[12];
static int canal_instalado = -2;        /* versión del canal (-1 no está; -2 sin mirar) */
static int n_items;
static long long restos;

static void abrir_menu(void) {
    restos = instalar_restos();
    n_items = 0;
    items[n_items++] = MI_ACCESO;
    if (juegos_con_version_nueva()) items[n_items++] = MI_ACTUALIZAR_JUEGOS;
    items[n_items++] = MI_INSTALADOS;
    items[n_items++] = MI_RECARGAR;
    items[n_items++] = MI_BUSCAR_APP;
    canal_instalado = canal_version_instalada();
    items[n_items++] = MI_CANAL;
    if (restos > 0) items[n_items++] = MI_RESTOS;
    items[n_items++] = MI_ACERCA;
    items[n_items++] = MI_SALIR;
    menu_sel = 0;
    modo = M_MENU;
}

static void texto_item(ItemMenu i, char *s, size_t n) {
    char t[32];
    switch (i) {
    case MI_ACCESO:
        if (*ajuste("clave") && error_catalogo != 401) snprintf(s, n, "Acceso a la librería completa: sí");
        else if (*ajuste("acceso_pendiente")) snprintf(s, n, "Acceso pedido: comprobar si lo han aceptado");
        else snprintf(s, n, "Solicitar acceso a esta librería");
        break;
    case MI_INSTALADOS: snprintf(s, n, "Ver solo lo instalado: %s", solo_instalados ? "sí" : "no"); break;
    case MI_RECARGAR: snprintf(s, n, "Recargar la librería"); break;
    case MI_ACTUALIZAR_JUEGOS: snprintf(s, n, "Actualizar juegos con versión nueva (%d)", juegos_con_version_nueva()); break;
    case MI_BUSCAR_APP: snprintf(s, n, "Buscar actualizaciones de RomsLibrary"); break;
    case MI_CANAL:
        if (canal_instalado < 0) snprintf(s, n, "Instalar como canal (menú de Wii)");
        else if (canal_version_publicada > canal_instalado) snprintf(s, n, "Actualizar el canal");
        else snprintf(s, n, "Canal instalado ✓ (reinstalar)");
        break;
    case MI_RESTOS:
        texto_tamano(restos, t, sizeof t);
        snprintf(s, n, "Borrar descargas sin terminar (%s)", t);
        break;
    case MI_ACERCA: snprintf(s, n, "Acerca de RomsLibrary"); break;
    case MI_SALIR: snprintf(s, n, "Salir"); break;
    }
}

static void elegir_menu(void) {
    char e[300];
    modo = M_LISTA;
    switch (items[menu_sel]) {
    case MI_ACCESO:
        if (*ajuste("clave") && error_catalogo != 401) {
            avisar("Esta Wii ya ve la librería completa.", C_ACENTO);
        } else if (*ajuste("acceso_pendiente")) {
            int r = acceso_comprobar(con);
            if (r > 0) {
                avisar("¡Acceso aceptado!", C_ACENTO);
                cargar_catalogo();
            } else if (r < 0) {
                dialogo("Acceso rechazado", "El administrador de la librería ha rechazado el acceso de esta Wii.");
            } else {
                avisar("Todavía sin respuesta: el administrador tiene que aceptarlo en el gestor.", C_TENUE);
            }
        } else {
            pantalla_espera("Pidiendo acceso…");
            if (acceso_solicitar(con, e, sizeof e)) dialogo("No se pudo pedir acceso", e);
            else {
                char nombre[64], t[400];
                plat_nombre_consola(nombre, sizeof nombre);
                snprintf(t, sizeof t,
                         "Solicitud enviada como «%s». Acéptala en el gestor de la librería (Accesos) y vuelve a "
                         "este menú para comprobarlo.",
                         nombre);
                dialogo("Acceso pedido", t);
            }
        }
        break;
    case MI_INSTALADOS:
        solo_instalados = !solo_instalados;
        sel = primera = 0;
        calcular_entradas();
        break;
    case MI_RECARGAR: cargar_catalogo(); break;
    case MI_ACTUALIZAR_JUEGOS:
        confirmar = C_ACTUALIZAR_JUEGOS;
        modo = M_CONFIRMAR;
        break;
    case MI_BUSCAR_APP: {
        pantalla_espera("Buscando actualizaciones…");
        int r = actualizacion_buscar(con, version_nueva, sizeof version_nueva);
        if (r > 0) {
            confirmar = C_ACTUALIZAR;
            modo = M_CONFIRMAR;
        } else {
            avisar(r < 0 ? "No se pudo consultar sund.es." : "Tienes la última versión (" RL_VERSION ").", r < 0 ? C_NARANJA : C_ACENTO);
        }
        break;
    }
    case MI_RESTOS:
        confirmar = C_BORRAR_RESTOS;
        modo = M_CONFIRMAR;
        break;
    case MI_CANAL: {
        EstadoDescargas d;
        instalar_estado(&d);
        if (d.activa || d.en_cola) {
            avisar("Espera a que terminen las descargas: instalar el canal cierra la app.", C_NARANJA);
            break;
        }
        int ios = *ajuste("ios_canal") ? atoi(ajuste("ios_canal")) : 249;
        if (canal_comprobar(ios, e, sizeof e)) {
            dialogo("No se puede instalar el canal", e);
            break;
        }
        confirmar = C_CANAL;
        modo = M_CONFIRMAR;
        break;
    }
    case MI_ACERCA: modo = M_ACERCA; break;
    case MI_SALIR: {
        EstadoDescargas d;
        instalar_estado(&d);
        if (d.activa || d.en_cola) {
            confirmar = C_SALIR;
            modo = M_CONFIRMAR;
        } else {
            salir = 1;
        }
        break;
    }
    }
}

/* ---------------- dibujo ---------------- */

static void dibujar_cabecera(void) {
    rect(0, 0, ANCHO, 52, C_PANEL);
    int wt = texto(20, 10, LETRA_TITULO, C_ACENTO, 0, "RomsLibrary");
    const char *url = ajuste("url");
    const char *host = strstr(url, "://") ? strstr(url, "://") + 3 : url;
    texto(20 + wt + 14, 18, LETRA_PEQUENA, C_TENUE, 200, host);
    if (plat_ms() > libre_cuando) {
        const char *raiz = instalar_raiz("wii");
        libre = raiz ? plat_libre(raiz) : -1;
        libre_cuando = plat_ms() + 15000;
    }
    char t[80], tam[32];
    const char *raiz = instalar_raiz("wii");
    if (!strcmp(ajuste("juegos"), "usb") && problema_usb && strstr(problema_usb, "FAT32")) {
        snprintf(t, sizeof t, "Disco USB no compatible");
        texto_derecha(ANCHO - 20, 18, LETRA_PEQUENA, C_ROJO, t);
    } else if (raiz && libre >= 0) {
        texto_tamano(libre, tam, sizeof tam);
        snprintf(t, sizeof t, "%s %s libres", plat_raiz("usb") && !strcmp(raiz, plat_raiz("usb")) ? "USB:" : "SD:", tam);
        texto_derecha(ANCHO - 20, 18, LETRA_PEQUENA, C_TEXTO, t);
    } else {
        texto_derecha(ANCHO - 20, 18, LETRA_PEQUENA, C_ROJO, "Sin USB ni SD");
    }

    /* Pestañas de consolas. */
    int x = 14;
    for (int i = 0; i < n_pestanas; i++) {
        const char *nom = SISTEMAS[pestanas[i]].nombre;
        int w = texto_ancho(LETRA_PEQUENA, nom) + 16;
        if (i == pestana) rect(x, 58, w, 24, C_BORDE);
        texto(x + 8, 61, LETRA_PEQUENA, i == pestana ? C_ACENTO : C_TENUE, 0, nom);
        x += w + 4;
    }
    if (n_pestanas > 1) texto_derecha(ANCHO - 14, 61, LETRA_PEQUENA, C_TENUE, "−/+ consola");
}

static void estado_fila(const Juego *j, char *t, size_t n, unsigned *color) {
    double p = instalar_progreso(j);
    char motivo[200];
    if (p >= 0) {
        snprintf(t, n, "%d%%", (int)(p * 100));
        *color = C_ACENTO;
    } else if (instalar_en_cola(j)) {
        snprintf(t, n, "En cola");
        *color = C_TENUE;
    } else if (j->instalado && j->actualizable) {
        snprintf(t, n, "↑ Versión nueva");
        *color = C_AMARILLO;
    } else if (j->instalado) {
        snprintf(t, n, "✓ Instalado");
        *color = C_ACENTO;
    } else if (!j->descargable) {
        snprintf(t, n, "✗ Corrupto");
        *color = C_ROJO;
    } else if (!instalar_compatible(j, motivo, sizeof motivo)) {
        snprintf(t, n, "No compatible");
        *color = C_TENUE;
    } else {
        texto_tamano(j->tamano, t, n);
        *color = C_TENUE;
    }
}

static void dibujar_lista(void) {
    rect(10, LISTA_Y - 4, LISTA_ANCHO, FILAS * ALTO_FILA + 8, C_PANEL);
    if (!n_entradas) {
        const char *t = error_catalogo_texto[0] ? error_catalogo_texto
                        : solo_instalados        ? "No hay nada instalado de esta consola."
                        : !*ajuste("clave") && !*ajuste("acceso_pendiente")
                            ? "Sin acceso solo se ven los juegos que la librería comparte. Si es tu librería, pulsa 1 → "
                              "«Solicitar acceso» y acéptalo en su gestor."
                        : *ajuste("acceso_pendiente")
                            ? "Has pedido acceso: cuando lo acepten en el gestor, pulsa 1 para comprobarlo."
                            : "No hay juegos de esta consola en la librería.";
        parrafo(24, LISTA_Y + 10, LETRA_NORMAL, error_catalogo ? C_NARANJA : C_TENUE, LISTA_ANCHO - 28, t);
        return;
    }
    for (int f = 0; f < FILAS && primera + f < n_entradas; f++) {
        int i = primera + f, y = LISTA_Y + f * ALTO_FILA;
        const Juego *j = &cat.juegos[entradas[i]];
        if (i == sel) {
            rect(10, y - 2, LISTA_ANCHO, ALTO_FILA, C_BORDE);
            rect(10, y - 2, 4, ALTO_FILA, C_ACENTO);
        }
        char est[40];
        unsigned color;
        estado_fila(j, est, sizeof est, &color);
        int we = texto_ancho(LETRA_PEQUENA, est);
        texto_derecha(LISTA_ANCHO, y + 3, LETRA_PEQUENA, color, est);
        int x = 22;
        if (j->corrupto) x += texto(x, y + 1, LETRA_NORMAL, j->corrupto_automatico ? C_NARANJA : C_ROJO, 0, "⚠ ");
        texto(x, y + 1, LETRA_NORMAL, i == sel ? C_TEXTO : 0xc8cdd3, LISTA_ANCHO - x - we - 6, j->nombre);
    }
    if (n_entradas > FILAS) {
        /* Barra de desplazamiento. */
        int alto = FILAS * ALTO_FILA, h = alto * FILAS / n_entradas, y = LISTA_Y + (alto - h) * primera / (n_entradas - FILAS);
        rect(LISTA_ANCHO + 6, y, 3, h < 12 ? 12 : h, C_BORDE);
    }
}

static void dibujar_detalle(void) {
    int x = ANCHO - 212, w = ANCHO - x - 12;
    rect(x - 6, LISTA_Y - 4, w + 12, FILAS * ALTO_FILA + 8, C_PANEL);
    const Juego *j = juego_sel();
    if (!j) return;
    /* Carátula: se pide cuando la selección lleva un momento quieta. */
    if (portada_de != entradas[sel]) {
        soltar_portada();
        portada_de = entradas[sel];
        portada_desde = plat_ms() + 250;
    }
    if (!portada && j->portada[0] && plat_ms() >= portada_desde) {
        char ruta[400];
        if (caratula(j->portada, clave_de(j), ruta, sizeof ruta)) {
            portada = imagen_cargar(ruta, w, 190, &portada_w, &portada_h);
            if (!portada) portada_desde = (uint64_t)-1;      /* imagen que no se puede leer */
        } else {
            portada_desde = plat_ms() + 400;
        }
    }
    int y = LISTA_Y + 2;
    if (portada) {
        imagen(portada, x + (w - portada_w) / 2, y, portada_w, portada_h);
        y += portada_h + 8;
    } else {
        rect(x + w / 2 - 60, y, 120, 150, C_BORDE);
        texto(x + w / 2 - texto_ancho(LETRA_PEQUENA, nombre_sistema(j->sistema)) / 2, y + 66, LETRA_PEQUENA, C_TENUE, 0,
              nombre_sistema(j->sistema));
        y += 158;
    }
    y += parrafo(x, y, LETRA_PEQUENA, C_TEXTO, w, j->nombre);
    char t[300], tam[32], idi[40];
    texto_tamano(j->tamano, tam, sizeof tam);
    etiqueta_idiomas(j, idi, sizeof idi);
    snprintf(t, sizeof t, "%s%s%s", tam, idi[0] ? " · " : "", idi);
    texto(x, y + 2, LETRA_PEQUENA, C_TENUE, w, t);
    y += 20;
    if (j->origen_url[0]) {
        const char *h = strstr(j->origen_url, "://") ? strstr(j->origen_url, "://") + 3 : j->origen_url;
        snprintf(t, sizeof t, "De %s", h);
        texto(x, y, LETRA_PEQUENA, C_AZUL, w, t);
        y += 18;
    }
    if (j->corrupto) {
        snprintf(t, sizeof t, "%s%s%s", j->corrupto_automatico ? "Posible archivo corrupto" : "Confirmado como corrupto",
                 j->motivo_corrupto[0] ? ": " : "", j->motivo_corrupto);
        y += parrafo(x, y, LETRA_PEQUENA, j->corrupto_automatico ? C_NARANJA : C_ROJO, w, t);
    }
    char motivo[300];
    if (!j->instalado && !instalar_compatible(j, motivo, sizeof motivo)) y += parrafo(x, y, LETRA_PEQUENA, C_TENUE, w, motivo);
    (void)y;
}

static void dibujar_pie(void) {
    EstadoDescargas d;
    instalar_estado(&d);
    if (d.cambios != cambios_vistos) {
        cambios_vistos = d.cambios;
        if (d.aviso[0]) avisar(d.aviso, d.aviso_es_error ? C_ROJO : C_ACENTO);
        refrescar_instalados();
    }
    int y = 424;
    if (d.activa) {
        rect(10, y, ANCHO - 20, 20, C_PANEL);
        if (d.total > 0) rect(10, y, (int)((ANCHO - 20) * (double)d.hecho / (double)d.total), 20, 0x1d5c34);
        char t[300], vel[32] = "", resto[48] = "";
        if (d.velocidad > 0) {
            snprintf(vel, sizeof vel, " · %.1f MB/s", d.velocidad / 1048576.0);
            if (d.total > d.hecho) {
                long s = (long)((double)(d.total - d.hecho) / d.velocidad);
                if (s >= 3600) snprintf(resto, sizeof resto, " · quedan %ld h %ld min", s / 3600, s / 60 % 60);
                else snprintf(resto, sizeof resto, " · quedan %ld min", s / 60 + 1);
            }
        }
        char cola[32] = "";
        if (d.en_cola) snprintf(cola, sizeof cola, " · %d en cola", d.en_cola);
        snprintf(t, sizeof t, "↓ %s · %d%%%s%s%s", d.nombre, d.total > 0 ? (int)(100.0 * d.hecho / d.total) : 0, vel, resto, cola);
        texto(16, y + 1, LETRA_PEQUENA, C_TEXTO, ANCHO - 32, t);
    } else if (plat_ms() < aviso_hasta) {
        texto(16, y + 1, LETRA_PEQUENA, aviso_color, ANCHO - 32, aviso_texto);
    }
    if (d.activa && plat_ms() < aviso_hasta) texto(16, y - 18, LETRA_PEQUENA, aviso_color, ANCHO - 32, aviso_texto);

    const Juego *j = juego_sel();
    char ayuda[200];
    char motivo[200];
    const char *a = !j                                         ? ""
                    : instalar_en_cola(j)                      ? "A Cancelar"
                    : j->instalado                             ? "A Opciones"
                    : instalar_compatible(j, motivo, sizeof motivo) && j->descargable ? "A Instalar"
                                                                                    : "";
    int nv = 0;
    if (j) {
        int *v;
        nv = catalogo_variantes(&cat, entradas[sel], &v);
        free(v);
    }
    snprintf(ayuda, sizeof ayuda, "%s%s%s1 Menú · HOME Salir", a, a[0] ? " · " : "", nv > 1 ? "2 Idioma · " : "");
    texto(16, 452, LETRA_PEQUENA, C_TENUE, ANCHO - 32, ayuda);
}

static void caja(int w, int h, const char *titulo) {
    int x = (ANCHO - w) / 2, y = (ALTO - h) / 2;
    rect_alfa(0, 0, ANCHO, ALTO, 0, 170);
    rect(x, y, w, h, C_PANEL);
    marco(x, y, w, h, C_ACENTO);
    texto(x + 18, y + 14, LETRA_GRANDE, C_ACENTO, w - 36, titulo);
}

static void dibujar_menu(void) {
    int w = 460, h = 70 + n_items * 32, x = (ANCHO - w) / 2, y = (ALTO - h) / 2;
    caja(w, h, "Menú");
    for (int i = 0; i < n_items; i++) {
        char t[120];
        texto_item(items[i], t, sizeof t);
        if (i == menu_sel) rect(x + 10, y + 50 + i * 32, w - 20, 28, C_BORDE);
        texto(x + 22, y + 53 + i * 32, LETRA_NORMAL, i == menu_sel ? C_TEXTO : C_TENUE, w - 44, t);
    }
}

static void dibujar_idiomas(void) {
    int w = 500, h = 80 + n_variantes * 32, x = (ANCHO - w) / 2, y = (ALTO - h) / 2;
    caja(w, h, "Elige el idioma");
    for (int i = 0; i < n_variantes; i++) {
        const Juego *v = &cat.juegos[variantes[i]];
        char idi[40], t[300];
        etiqueta_idiomas(v, idi, sizeof idi);
        snprintf(t, sizeof t, "%s%s%s%s", idi[0] ? idi : "¿?", " · ", v->fichero, v->instalado ? "  ✓" : "");
        if (i == idioma_sel) rect(x + 10, y + 50 + i * 32, w - 20, 28, C_BORDE);
        texto(x + 22, y + 53 + i * 32, LETRA_NORMAL, v->corrupto ? (v->corrupto_automatico ? C_NARANJA : C_ROJO) : i == idioma_sel ? C_TEXTO : C_TENUE,
              w - 44, t);
    }
    texto(x + 22, y + h - 24, LETRA_PEQUENA, C_TENUE, 0, "A Elegir · B Volver");
}

static void dibujar_confirmar(void) {
    const Juego *j = confirmar_juego >= 0 ? &cat.juegos[confirmar_juego] : NULL;
    char t[700], tam[32];
    const char *titulo = "¿Seguro?";
    switch (confirmar) {
    case C_BORRAR: {
        char donde[400];
        instalar_donde(j, donde, sizeof donde);
        texto_tamano(j->tamano, tam, sizeof tam);
        titulo = "Borrar del disco";
        snprintf(t, sizeof t, "Se borra «%s» (%s) de %s. Las partidas no se tocan: siguen en la Wii, en la tarjeta de "
                 "memoria de Nintendont o en la carpeta del emulador.", j->nombre, tam, donde);
        break;
    }
    case C_INSTALAR_CORRUPTO:
        titulo = j->corrupto_automatico ? "Posible archivo corrupto" : "Archivo corrupto";
        snprintf(t, sizeof t, "%s%s%s ¿Instalarlo igualmente?",
                 j->corrupto_automatico ? "La librería sospecha que este fichero está dañado." : "El administrador ha confirmado que este fichero está dañado.",
                 j->motivo_corrupto[0] ? " " : "", j->motivo_corrupto);
        break;
    case C_SALIR:
        titulo = "Hay descargas en curso";
        snprintf(t, sizeof t, "Si sales ahora, lo que se esté bajando seguirá donde lo dejó la próxima vez que lo "
                 "instales. ¿Salir?");
        break;
    case C_ACTUALIZAR:
        titulo = "Hay una versión nueva";
        snprintf(t, sizeof t, "RomsLibrary %s ya está disponible (tienes la %s). ¿Actualizar ahora?", version_nueva, RL_VERSION);
        break;
    case C_ACTUALIZAR_JUEGOS:
        titulo = "Actualizar juegos";
        snprintf(t, sizeof t, "Se bajan las versiones nuevas de %d juegos y, cuando cada una está entera, sustituye a "
                 "la instalada. Las partidas no se tocan. ¿Seguir?", juegos_con_version_nueva());
        break;
    case C_CANAL:
        titulo = "Instalar como canal";
        snprintf(t, sizeof t, "Se instala el canal RomsLibrary en el menú de Wii (con el cIOS de d2x). Abre la app de "
                 "apps/romslibrary, así que no hay que reinstalarlo cuando se actualice. Al terminar, la app se cierra. "
                 "Recomendado: copia de la NAND con BootMii y Priiloader. ¿Seguir?");
        break;
    case C_BORRAR_RESTOS:
        texto_tamano(restos, tam, sizeof tam);
        titulo = "Descargas sin terminar";
        snprintf(t, sizeof t, "Se borran %s de descargas a medias (no se podrán continuar).", tam);
        break;
    default:
        t[0] = 0;
    }
    int w = 520, x = (ANCHO - w) / 2, y = 130;
    caja(w, 220, titulo);
    parrafo(x + 18, y + 54, LETRA_NORMAL, C_TEXTO, w - 36, t);
    texto(x + 18, y + 190, LETRA_NORMAL, C_ACENTO, 0, "A Sí · B No");
}

static const char *texto_opcion(int o) {
    return o == 0 ? "Actualizar a la versión nueva" : o == 1 ? "Borrar del disco" : "Volver";
}

static void dibujar_opciones(void) {
    const Juego *j = &cat.juegos[confirmar_juego];
    int w = 460, h = 110 + n_opciones * 32, x = (ANCHO - w) / 2, y = (ALTO - h) / 2;
    caja(w, h, j->nombre);
    char donde[400];
    instalar_donde(j, donde, sizeof donde);
    texto(x + 18, y + 46, LETRA_PEQUENA, C_TENUE, w - 36, donde);
    for (int i = 0; i < n_opciones; i++) {
        if (i == opcion_sel) rect(x + 10, y + 74 + i * 32, w - 20, 28, C_BORDE);
        texto(x + 22, y + 77 + i * 32, LETRA_NORMAL, opciones[i] == 0 ? C_AMARILLO : i == opcion_sel ? C_TEXTO : C_TENUE,
              w - 44, texto_opcion(opciones[i]));
    }
}

static void elegir_opcion(void) {
    const Juego *j = &cat.juegos[confirmar_juego];
    char e[300];
    modo = M_LISTA;
    switch (opciones[opcion_sel]) {
    case 0:
        if (instalar_actualizar(j, e, sizeof e)) avisar(e, C_ROJO);
        else avisar("Actualización en cola: se sustituye cuando esté entera.", C_ACENTO);
        break;
    case 1:
        confirmar = C_BORRAR;
        modo = M_CONFIRMAR;
        break;
    }
}

static void dibujar_aviso(void) {
    int w = 520, x = (ANCHO - w) / 2;
    caja(w, 220, titulo_mensaje);
    parrafo(x + 18, 184, LETRA_NORMAL, C_TEXTO, w - 36, mensaje);
    texto(x + 18, 320, LETRA_NORMAL, C_ACENTO, 0, "A Aceptar");
}

static void dibujar_acerca(void) {
    int w = 580, x = (ANCHO - w) / 2;
    caja(w, 400, "RomsLibrary " RL_VERSION " para Wii");
    char t[1200];
    snprintf(t, sizeof t,
             "Software libre (MIT) · https://sund.es/romslibrary\n"
             "Librería: %s\nCarpeta de la app: %s\n\n"
             "Normas: no está permitido publicar como públicos juegos con copyright; los juegos privados deben "
             "obtenerse de manera legítima (p. ej., la copia de seguridad de tu original). No nos "
             "responsabilizamos de los usos contrarios a estas normas. Ver sund.es/romslibrary/aviso-legal.\n\n"
             "Incluye mbedTLS (Apache-2.0), SDL2, SDL2_ttf y SDL2_image (zlib), FreeType (FTL), libpng, libjpeg "
             "(IJG), zlib, jansson (MIT), libogc, las fuentes DejaVu y los certificados raíz de Mozilla (MPL-2.0). "
             "Textos completos en LICENCIAS.txt, junto a boot.dol.",
             ajuste("url"), plat_carpeta_app());
    parrafo(x + 18, 96, LETRA_PEQUENA, C_TEXTO, w - 36, t);
    texto(x + 18, 410, LETRA_NORMAL, C_ACENTO, 0, "B Volver");
}

/* ---------------- entrada ---------------- */

static void mover(int d) {
    if (!n_entradas) return;
    sel += d;
    if (sel < 0) sel = d < -1 ? 0 : n_entradas - 1;
    if (sel >= n_entradas) sel = d > 1 ? n_entradas - 1 : 0;
    if (sel < primera) primera = sel;
    if (sel >= primera + FILAS) primera = sel - FILAS + 1;
}

static void cambiar_pestana(int d) {
    if (n_pestanas < 2) return;
    pestana = (pestana + d + n_pestanas) % n_pestanas;
    sel = primera = 0;
    free(entradas);
    entradas = NULL;
    n_entradas = 0;
    calcular_entradas();
}

static void entrada(unsigned p, unsigned m) {
    /* Repetición al mantener arriba/abajo. */
    static uint64_t desde, siguiente;
    unsigned rep = 0;
    unsigned dir = m & (BOTON_ARRIBA | BOTON_ABAJO);
    if (p & dir) desde = plat_ms(), siguiente = desde + 380;
    else if (dir && plat_ms() >= siguiente) {
        rep = dir;
        siguiente = plat_ms() + 70;
    }
    unsigned arriba = (p | rep) & BOTON_ARRIBA, abajo = (p | rep) & BOTON_ABAJO;

    switch (modo) {
    case M_LISTA:
        if (arriba) mover(-1);
        if (abajo) mover(1);
        if (p & BOTON_IZQUIERDA) mover(-FILAS);
        if (p & BOTON_DERECHA) mover(FILAS);
        if (p & BOTON_ANTERIOR) cambiar_pestana(-1);
        if (p & BOTON_SIGUIENTE) cambiar_pestana(1);
        if (p & BOTON_A) accion_a();
        if (p & BOTON_MENU) abrir_menu();
        if ((p & BOTON_IDIOMA) && juego_sel()) {
            free(variantes);
            n_variantes = catalogo_variantes(&cat, entradas[sel], &variantes);
            if (n_variantes > 1) {
                idioma_sel = 0;
                for (int i = 0; i < n_variantes; i++)
                    if (variantes[i] == entradas[sel]) idioma_sel = i;
                modo = M_IDIOMAS;
            }
        }
        if (p & BOTON_SALIR) {
            items[0] = MI_SALIR;
            menu_sel = 0;
            elegir_menu();
        }
        break;
    case M_MENU:
        if (arriba) menu_sel = (menu_sel + n_items - 1) % n_items;
        if (abajo) menu_sel = (menu_sel + 1) % n_items;
        if (p & BOTON_A) elegir_menu();
        if (p & (BOTON_B | BOTON_MENU)) modo = M_LISTA;
        break;
    case M_IDIOMAS:
        if (arriba) idioma_sel = (idioma_sel + n_variantes - 1) % n_variantes;
        if (abajo) idioma_sel = (idioma_sel + 1) % n_variantes;
        if (p & BOTON_A) {
            const Juego *v = &cat.juegos[variantes[idioma_sel]];
            if (preferida) preferida[v->grupo] = variantes[idioma_sel];
            entradas[sel] = variantes[idioma_sel];
            soltar_portada();
            modo = M_LISTA;
        }
        if (p & (BOTON_B | BOTON_IDIOMA)) modo = M_LISTA;
        break;
    case M_CONFIRMAR:
        if (p & BOTON_A) confirmar_si();
        else if (p & BOTON_B) modo = M_LISTA;
        break;
    case M_AVISO:
        if (p & (BOTON_A | BOTON_B)) {
            modo = M_LISTA;
            if (confirmar == C_SALIR) salir = 1;
        }
        break;
    case M_ACERCA:
        if (p & (BOTON_A | BOTON_B)) modo = M_LISTA;
        break;
    case M_OPCIONES:
        if (arriba) opcion_sel = (opcion_sel + n_opciones - 1) % n_opciones;
        if (abajo) opcion_sel = (opcion_sel + 1) % n_opciones;
        if (p & BOTON_A) elegir_opcion();
        if (p & BOTON_B) modo = M_LISTA;
        break;
    }
}

int main(int argc, char **argv) {
    char error[300];
    plat_iniciar(argc, argv);
    if (dibujo_iniciar(plat_panoramica(), error, sizeof error)) {
        fprintf(stderr, "%s\n", error);
        return 1;
    }
    if (!plat_raiz("usb")) {
        pantalla_espera("Buscando el disco USB…");
        plat_esperar_usb();
    }
    ajustes_cargar();
    problema_usb = plat_problema_usb();
    pantalla_espera("Conectando a la red…");
    if (plat_red_iniciar(error, sizeof error) || red_iniciar(error, sizeof error)) {
        dialogo("Sin conexión", error);
        confirmar = C_SALIR;
    }
    instalar_iniciar();
    caratulas_iniciar();
    con = con_nueva();
    if (modo != M_AVISO) {
        pantalla_espera("Buscando actualizaciones…");
        if (actualizacion_buscar(con, version_nueva, sizeof version_nueva) > 0) {
            confirmar = C_ACTUALIZAR;
            modo = M_CONFIRMAR;
        }
        int acceso = acceso_comprobar(con);
        cargar_catalogo();
        int canal = canal_version_instalada();
        if (canal >= 0 && canal_version_publicada > canal) avisar("Hay una versión nueva del canal: 1 → Actualizar el canal.", C_AMARILLO);
        int nuevas = juegos_con_version_nueva();
        if (nuevas) {
            char t[120];
            snprintf(t, sizeof t, "%d %s versión nueva: 1 → Actualizar juegos.", nuevas, nuevas == 1 ? "juego tiene" : "juegos tienen");
            avisar(t, C_AMARILLO);
        }
        if (acceso > 0) avisar("¡Acceso a la librería aceptado!", C_ACENTO);
        if (problema_usb && !strcmp(ajuste("juegos"), "usb")) {
            char t[400];
            snprintf(t, sizeof t, "%s Mientras tanto, los juegos irán a la SD.", problema_usb);
            if (strstr(problema_usb, "FAT32") && modo == M_LISTA) dialogo("Disco USB no compatible", t);
            else avisar(t, C_NARANJA);
        }
        if (acceso < 0) avisar("El administrador ha rechazado el acceso de esta Wii.", C_NARANJA);
    }

    while (!salir && !plat_debe_salir()) {
        unsigned p, m;
        plat_leer_botones(&p, &m);
        entrada(p, m);
        limpiar(C_FONDO);
        dibujar_cabecera();
        dibujar_lista();
        dibujar_detalle();
        dibujar_pie();
        if (modo == M_MENU) dibujar_menu();
        if (modo == M_IDIOMAS) dibujar_idiomas();
        if (modo == M_CONFIRMAR) dibujar_confirmar();
        if (modo == M_AVISO) dibujar_aviso();
        if (modo == M_ACERCA) dibujar_acerca();
        if (modo == M_OPCIONES) dibujar_opciones();
        presentar();
    }
    pantalla_espera("Saliendo…");
    caratulas_parar();
    instalar_parar();
    con_liberar(con);
    soltar_portada();
    dibujo_terminar();
    plat_terminar();
    return 0;
}
