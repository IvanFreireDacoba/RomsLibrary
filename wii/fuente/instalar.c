#include "instalar.h"

#include <SDL2/SDL.h>
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <jansson.h>

#include "ajustes.h"
#include "plataforma.h"
#include "red.h"

#include <mbedtls/sha256.h>

/* FAT32 no admite ficheros de 4 GB: los .wbfs se parten aquí (lo que usan
 * USB Loader GX y Wii Backup Manager: 4 GB - 32 KB). */
#ifndef CORTE_WBFS
#define CORTE_WBFS 4294934528LL
#endif
#define MAX_FAT32 4294967295LL
#define MARGEN (64LL << 20)
#define MAX_COLA 64

typedef struct {
    Juego j;
    int reemplazar;           /* actualización: sustituye a lo instalado */
} Trabajo;

static SDL_mutex *cerrojo;
static SDL_cond *aviso_cola;
static SDL_Thread *hilo;
static Trabajo cola[MAX_COLA];
static int n_cola;
static Trabajo actual;
static volatile int hay_actual, cancelar_actual, parar;
static EstadoDescargas estado;

/* ---------------- índice de lo instalado ---------------- */

/* <carpeta de la app>/instalados.txt: «sistema \t fichero \t ruta \t sha \t tamaño» por línea. */
#define MAX_INSTALADOS 2048
static struct {
    char sistema[12], fichero[204], ruta[400], sha[65];
    long long tamano;
} indice[MAX_INSTALADOS];
static int n_indice, indice_cargado;

static void ruta_indice(char *s, size_t n) { snprintf(s, n, "%s/instalados.txt", plat_carpeta_app()); }

static void indice_cargar(void) {
    if (indice_cargado) return;
    indice_cargado = 1;
    char r[300], linea[900];
    ruta_indice(r, sizeof r);
    FILE *f = fopen(r, "r");
    if (!f) return;
    while (n_indice < MAX_INSTALADOS && fgets(linea, sizeof linea, f)) {
        linea[strcspn(linea, "\r\n")] = 0;
        char *campo[5] = {linea};
        int n = 1;
        for (char *p = linea; n < 5 && (p = strchr(p, '\t')); n++) {
            *p++ = 0;
            campo[n] = p;
        }
        if (n < 3) continue;
        snprintf(indice[n_indice].sistema, sizeof indice[0].sistema, "%s", campo[0]);
        snprintf(indice[n_indice].fichero, sizeof indice[0].fichero, "%s", campo[1]);
        snprintf(indice[n_indice].ruta, sizeof indice[0].ruta, "%s", campo[2]);
        snprintf(indice[n_indice].sha, sizeof indice[0].sha, "%s", n > 3 ? campo[3] : "");
        indice[n_indice].tamano = n > 4 ? strtoll(campo[4], NULL, 10) : 0;
        n_indice++;
    }
    fclose(f);
}

static void indice_guardar(void) {
    char r[300], tmp[310];
    ruta_indice(r, sizeof r);
    snprintf(tmp, sizeof tmp, "%s.nuevo", r);
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    for (int i = 0; i < n_indice; i++)
        fprintf(f, "%s\t%s\t%s\t%s\t%lld\n", indice[i].sistema, indice[i].fichero, indice[i].ruta, indice[i].sha,
                indice[i].tamano);
    if (fclose(f) == 0) {
        remove(r);
        rename(tmp, r);
    }
}

static int indice_buscar(const char *sistema, const char *fichero) {
    indice_cargar();
    for (int i = 0; i < n_indice; i++)
        if (!strcmp(indice[i].sistema, sistema) && !strcmp(indice[i].fichero, fichero)) return i;
    return -1;
}

static void indice_poner(const char *sistema, const char *fichero, const char *ruta, const char *sha, long long tamano) {
    int i = indice_buscar(sistema, fichero);
    if (i < 0) {
        if (n_indice == MAX_INSTALADOS) return;
        i = n_indice++;
    }
    snprintf(indice[i].sistema, sizeof indice[0].sistema, "%s", sistema);
    snprintf(indice[i].fichero, sizeof indice[0].fichero, "%s", fichero);
    snprintf(indice[i].ruta, sizeof indice[0].ruta, "%s", ruta);
    snprintf(indice[i].sha, sizeof indice[0].sha, "%s", sha);
    indice[i].tamano = tamano;
    indice_guardar();
}

static void indice_quitar(const char *sistema, const char *fichero) {
    int i = indice_buscar(sistema, fichero);
    if (i < 0) return;
    indice[i] = indice[--n_indice];
    indice_guardar();
}

static int existe(const char *ruta) {
    struct stat s;
    return stat(ruta, &s) == 0;
}

static long long tamano_de(const char *ruta) {
    struct stat s;
    return stat(ruta, &s) == 0 ? (long long)s.st_size : -1;
}

int instalar_esta_instalado(const Juego *j) {
    SDL_LockMutex(cerrojo);
    int i = indice_buscar(j->sistema, j->fichero);
    int r = i >= 0 && existe(indice[i].ruta);
    SDL_UnlockMutex(cerrojo);
    return r;
}

int instalar_hay_version_nueva(const Juego *j) {
    SDL_LockMutex(cerrojo);
    int i = indice_buscar(j->sistema, j->fichero), r = 0;
    if (i >= 0 && existe(indice[i].ruta)) {
        if (indice[i].sha[0] && j->sha[0]) r = strcmp(indice[i].sha, j->sha) != 0;
        else if (indice[i].tamano > 0) r = indice[i].tamano != j->tamano;
        if (!r && (!indice[i].sha[0] || !indice[i].tamano)) {
            /* Instalado sin SHA/tamaño apuntados: se da por la versión actual. */
            snprintf(indice[i].sha, sizeof indice[0].sha, "%s", j->sha);
            indice[i].tamano = j->tamano;
            indice_guardar();
        }
    }
    SDL_UnlockMutex(cerrojo);
    return r;
}

void instalar_donde(const Juego *j, char *salida, size_t n) {
    SDL_LockMutex(cerrojo);
    int i = indice_buscar(j->sistema, j->fichero);
    snprintf(salida, n, "%s", i >= 0 && existe(indice[i].ruta) ? indice[i].ruta : "");
    SDL_UnlockMutex(cerrojo);
}

/* ---------------- destinos ---------------- */

static int es_disco_nintendo(const char *s) { return !strcmp(s, "wii") || !strcmp(s, "gc"); }

const char *instalar_raiz(const char *sistema) {
    const char *dispositivo = ajuste(es_disco_nintendo(sistema) ? "juegos" : "roms");
    const char *r = plat_raiz(dispositivo);
    if (!r) r = plat_raiz(!strcmp(dispositivo, "usb") ? "sd" : "usb");     /* la otra, si solo hay una */
    return r;
}

static void carpeta_roms(const char *sistema, char *salida, size_t n) {
    char clave[40];
    snprintf(clave, sizeof clave, "carpeta_%s", sistema);
    const char *propia = ajuste(clave);
    const char *defecto = !strcmp(sistema, "n64") ? "/not64/roms" : !strcmp(sistema, "psx") ? "/wiisx/isos" : "/vbagx/roms";
    snprintf(salida, n, "%s%s", instalar_raiz(sistema), *propia ? propia : defecto);
}

static const char *extension(const char *f) {
    const char *p = strrchr(f, '.');
    return p ? p : "";
}

int instalar_compatible(const Juego *j, char *motivo, size_t n) {
    const char *e = extension(j->fichero);
    if (!instalar_raiz(j->sistema)) {
        snprintf(motivo, n, "No hay ni USB ni SD montados.");
        return 0;
    }
    if (!strcmp(j->sistema, "wii")) {
        if (j->es_grupo) {
            if (!strcasecmp(e, ".wbfs")) return 1;
        } else if (!strcasecmp(e, ".wbfs")) {
            return 1;
        } else if (!strcasecmp(e, ".iso")) {
            if (j->tamano > MAX_FAT32) {
                snprintf(motivo, n, "Es un .iso de más de 4 GB: no cabe en un disco FAT32. Súbelo en .wbfs.");
                return 0;
            }
            return 1;
        }
        snprintf(motivo, n, "USB Loader GX no carga este formato (%s).", e);
        return 0;
    }
    if (!strcmp(j->sistema, "gc")) {
        if (!j->es_grupo && (!strcasecmp(e, ".iso") || !strcasecmp(e, ".gcm"))) return 1;
        snprintf(motivo, n, "Nintendont solo carga .iso/.gcm (este es %s).", j->es_grupo ? "de varios ficheros" : e);
        return 0;
    }
    if (!strcmp(j->sistema, "n64")) {
        if (!j->es_grupo && (!strcasecmp(e, ".z64") || !strcasecmp(e, ".n64") || !strcasecmp(e, ".v64"))) return 1;
        snprintf(motivo, n, "Not64 no carga este formato (%s).", e);
        return 0;
    }
    if (!strcmp(j->sistema, "psx")) {
        if (j->es_grupo || !strcasecmp(e, ".iso") || !strcasecmp(e, ".cue")) return 1;
        snprintf(motivo, n, "WiiSX no carga este formato (%s).", e);
        return 0;
    }
    return 1;                                       /* GB/GBC/GBA (también .zip) */
}

void nombre_fat(const char *texto, char *salida, size_t n) {
    size_t j = 0;
    for (const unsigned char *p = (const unsigned char *)texto; *p && j + 1 < n; p++)
        salida[j++] = (*p < 32 || strchr("/\\:*?\"<>|", *p)) ? '_' : (char)*p;
    while (j && (salida[j - 1] == ' ' || salida[j - 1] == '.')) j--;
    salida[j] = 0;
    if (!j) snprintf(salida, n, "Juego");
}

static void crear_carpetas(const char *ruta) {
    char r[512];
    snprintf(r, sizeof r, "%s", ruta);
    char *p = strchr(r, '/');
    while (p) {
        *p = 0;
        if (r[0] && r[strlen(r) - 1] != ':') mkdir(r, 0777);
        *p = '/';
        p = strchr(p + 1, '/');
    }
    mkdir(r, 0777);
}

int borrar_recursivo(const char *ruta) {
    struct stat s;
    if (stat(ruta, &s) != 0) return 0;
    if (!S_ISDIR(s.st_mode)) return remove(ruta);
    DIR *d = opendir(ruta);
    if (!d) return -1;
    struct dirent *e;
    char hijo[600];
    int r = 0;
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        snprintf(hijo, sizeof hijo, "%s/%s", ruta, e->d_name);
        r |= borrar_recursivo(hijo);
    }
    closedir(d);
    return r | rmdir(ruta);
}

static unsigned long long fnv(const char *a, const char *b) {
    unsigned long long h = 1469598103934665603ULL;
    for (const char *p = a; *p; p++) h = (h ^ (unsigned char)*p) * 1099511628211ULL;
    h = (h ^ '/') * 1099511628211ULL;
    for (const char *p = b; *p; p++) h = (h ^ (unsigned char)*p) * 1099511628211ULL;
    return h;
}

static void carpeta_temporal(const Juego *j, char *salida, size_t n) {
    snprintf(salida, n, "%s/romslibrary-descargas/%016llx", instalar_raiz(j->sistema), fnv(j->sistema, j->fichero));
}

/* ---------------- descargar un fichero (con reanudación y partes) ---------------- */

typedef struct {
    char base[480];           /* ruta final sin «.descargando» */
    int partir;               /* .wbfs: partes .wbf1, .wbf2... */
    FILE *f;
    int parte;
    long long en_parte;
    char *buf;
    size_t lleno;
} Escritor;

#define TAM_BUF (512 * 1024)

static void nombre_parte(const Escritor *w, int parte, char *salida, size_t n) {
    if (parte == 0) snprintf(salida, n, "%s.descargando", w->base);
    else {
        /* "X.wbfs" -> "X.wbf1" */
        char b[480];
        snprintf(b, sizeof b, "%s", w->base);
        char *punto = strrchr(b, '.');
        if (punto) *punto = 0;
        snprintf(salida, n, "%s.wbf%d.descargando", b, parte);
    }
}

/* Lo que ya hay descargado (todas las partes). */
static long long escritor_existente(Escritor *w) {
    long long total = 0;
    char r[520];
    for (int p = 0;; p++) {
        nombre_parte(w, p, r, sizeof r);
        long long t = tamano_de(r);
        if (t < 0) break;
        total += t;
        if (!w->partir || t < CORTE_WBFS) break;
    }
    return total;
}

static void escritor_borrar(Escritor *w) {
    char r[520];
    for (int p = 0; p < 8; p++) {
        nombre_parte(w, p, r, sizeof r);
        remove(r);
    }
}

static int escritor_abrir(Escritor *w, long long desde) {
    w->buf = malloc(TAM_BUF);
    w->lleno = 0;
    if (!w->buf) return -1;
    if (desde == 0) escritor_borrar(w);
    w->parte = w->partir ? (int)(desde / CORTE_WBFS) : 0;
    w->en_parte = w->partir ? desde % CORTE_WBFS : desde;
    char r[520];
    nombre_parte(w, w->parte, r, sizeof r);
    w->f = fopen(r, w->en_parte ? "r+b" : "wb");
    if (w->f && w->en_parte && fseeko(w->f, w->en_parte, SEEK_SET)) {
        fclose(w->f);
        w->f = NULL;
    }
    return w->f ? 0 : -1;
}

static int escritor_vaciar(Escritor *w) {
    if (w->lleno && fwrite(w->buf, 1, w->lleno, w->f) != w->lleno) return -1;
    w->lleno = 0;
    return 0;
}

static int escritor_escribir(Escritor *w, const char *d, size_t n) {
    while (n) {
        if (w->partir && w->en_parte == CORTE_WBFS) {
            if (escritor_vaciar(w) || fclose(w->f)) {
                w->f = NULL;
                return -1;
            }
            char r[520];
            nombre_parte(w, ++w->parte, r, sizeof r);
            w->f = fopen(r, "wb");
            if (!w->f) return -1;
            w->en_parte = 0;
        }
        size_t cabe = TAM_BUF - w->lleno;
        if (w->partir && (long long)cabe > CORTE_WBFS - w->en_parte - (long long)w->lleno)
            cabe = (size_t)(CORTE_WBFS - w->en_parte - (long long)w->lleno);
        if (cabe > n) cabe = n;
        memcpy(w->buf + w->lleno, d, cabe);
        w->lleno += cabe;
        d += cabe;
        n -= cabe;
        if (w->lleno == TAM_BUF || (w->partir && w->en_parte + (long long)w->lleno == CORTE_WBFS)) {
            long long l = (long long)w->lleno;
            if (escritor_vaciar(w)) return -1;
            w->en_parte += l;
        }
    }
    return 0;
}

static int escritor_cerrar(Escritor *w) {
    int r = 0;
    if (w->f) {
        r = escritor_vaciar(w);
        r |= fclose(w->f);
    }
    w->f = NULL;
    free(w->buf);
    w->buf = NULL;
    return r;
}

/* Quita «.descargando» a todas las partes. Devuelve cuántas partes hay. */
static int escritor_terminar(Escritor *w) {
    char r[520], final[520];
    int p;
    for (p = 0; p < 8; p++) {
        nombre_parte(w, p, r, sizeof r);
        if (!existe(r)) break;
        snprintf(final, sizeof final, "%.*s", (int)(strlen(r) - strlen(".descargando")), r);
        remove(final);
        if (rename(r, final)) return -1;
    }
    return p;
}

static void progreso(long long hecho, long long total) {
    static uint64_t desde;
    static long long hecho_desde;
    uint64_t ahora = plat_ms();
    SDL_LockMutex(cerrojo);
    if (hecho < hecho_desde || !desde) {
        desde = ahora;
        hecho_desde = hecho;
    }
    if (ahora - desde >= 1500) {
        double v = (double)(hecho - hecho_desde) * 1000.0 / (double)(ahora - desde);
        estado.velocidad = estado.velocidad > 0 ? estado.velocidad * 0.6 + v * 0.4 : v;
        desde = ahora;
        hecho_desde = hecho;
    }
    estado.hecho = hecho;
    estado.total = total;
    SDL_UnlockMutex(cerrojo);
}

static void leer_mensaje_error(Conexion *c, int codigo, char *error, size_t n) {
    char b[1024];
    int l = 0, r;
    while (l < (int)sizeof b - 1 && (r = http_leer(c, b + l, (int)sizeof b - 1 - l)) > 0) l += r;
    b[l > 0 ? l : 0] = 0;
    http_cerrar(c);
    json_t *j = json_loads(b, 0, NULL);
    const char *m = j ? json_string_value(json_object_get(j, "message")) : NULL;
    if (!m && j) m = json_string_value(json_object_get(j, "detail"));
    if (codigo == 401) snprintf(error, n, "Esta librería necesita acceso: pide acceso en el menú (botón 1).");
    else if (codigo == 410) snprintf(error, n, "%s", m && *m ? m : "La librería ya no sirve este juego.");
    else if (m && *m) snprintf(error, n, "%s", m);
    else snprintf(error, n, "Error %d de la librería.", codigo);
    json_decref(j);
}

/* Descarga `url` a `w` (que ya tiene `base`), sumando `antes` al progreso. */
static int descargar(Conexion *c, const char *url, const char *clave, Escritor *w, long long antes,
                     long long total_trabajo, char *error, size_t n) {
    int reintentos = 0, redirecciones = 0;
    static char url_actual[2048];
    for (;;) {
        long long hecho = escritor_existente(w);
        Respuesta r;
        if (http_abrir(c, "GET", url, clave, hecho, NULL, &r, error, n)) goto reintentar;
        if (r.estado == 416 && hecho > 0) {
            http_cerrar(c);
            escritor_borrar(w);                /* lo parcial no cuadra: desde cero */
            continue;
        }
        if (r.estado >= 301 && r.estado <= 308 && r.ubicacion[0] && redirecciones++ < 5) {
            /* Las webs de los autores (GitHub, itch.io...) suelen redirigir al fichero. */
            http_cerrar(c);
            if (strncmp(r.ubicacion, "https://", 8) || strchr(r.ubicacion, '@')) {
                snprintf(error, n, "El enlace redirige a una dirección no válida.");
                return -1;
            }
            snprintf(url_actual, sizeof url_actual, "%s", r.ubicacion);
            url = url_actual;
            clave = NULL;                       /* la clave nunca sale de nuestra librería */
            continue;
        }
        if (r.estado != 200 && r.estado != 206) {
            leer_mensaje_error(c, r.estado, error, n);
            return -1;
        }
        if (hecho && r.estado == 200) hecho = 0;   /* no reanuda: desde cero */
        long long total = r.parcial ? r.total : r.longitud;
        if (escritor_abrir(w, hecho)) {
            http_cerrar(c);
            snprintf(error, n, "No se puede escribir en la unidad (¿llena o protegida?).");
            escritor_cerrar(w);
            return -1;
        }
        char b[16384];
        int l;
        while ((l = http_leer(c, b, sizeof b)) > 0) {
            if (cancelar_actual || parar || plat_debe_salir()) {
                escritor_cerrar(w);
                http_cerrar(c);
                snprintf(error, n, "Cancelado.");
                return -2;
            }
            if (escritor_escribir(w, b, (size_t)l)) {
                escritor_cerrar(w);
                http_cerrar(c);
                snprintf(error, n, "No se puede escribir en la unidad (¿llena?).");
                return -1;
            }
            hecho += l;
            progreso(antes + hecho, total_trabajo);
        }
        http_cerrar(c);
        if (escritor_cerrar(w)) {
            snprintf(error, n, "No se pudo terminar de escribir el fichero.");
            return -1;
        }
        if (l == 0 && (total < 0 || hecho == total)) return 0;
        snprintf(error, n, "Se cortó la descarga.");
    reintentar:
        if (cancelar_actual || parar || ++reintentos > 8) return -1;
        plat_dormir_ms(reintentos < 4 ? 2000 : 8000);
    }
}

/* ID de 6 caracteres de un disco de Wii/GameCube (.iso; .wbfs en 0x200). */
static int leer_id(const char *ruta, char id[7]) {
    unsigned char cab[0x210];
    FILE *f = fopen(ruta, "rb");
    if (!f) return -1;
    size_t l = fread(cab, 1, sizeof cab, f);
    fclose(f);
    const unsigned char *d = cab;
    if (l >= 0x206 && !memcmp(cab, "WBFS", 4)) d = cab + 0x200;
    else if (l < 6) return -1;
    for (int i = 0; i < 6; i++) {
        if (!isalnum(d[i])) return -1;
        id[i] = (char)d[i];
    }
    id[6] = 0;
    return 0;
}

static void sin_extension(const char *f, char *s, size_t n) {
    snprintf(s, n, "%s", f);
    char *p = strrchr(s, '.');
    if (p && p != s) *p = 0;
}

static int ruta_permitida(const Juego *j, const char *donde);

/* SHA-256 de lo descargado (todas las partes, aún con «.descargando»). */
static int sha_descargado(Escritor *w, char hex[65]) {
    mbedtls_sha256_context s;
    unsigned char *b = malloc(1 << 18), h[32];
    if (!b) return -1;
    mbedtls_sha256_init(&s);
    mbedtls_sha256_starts(&s, 0);
    char r[520];
    for (int p = 0; p < 8; p++) {
        nombre_parte(w, p, r, sizeof r);
        FILE *f = fopen(r, "rb");
        if (!f) break;
        size_t l;
        while ((l = fread(b, 1, 1 << 18, f)) > 0) {
            if (cancelar_actual || parar) {
                fclose(f);
                free(b);
                return -2;
            }
            mbedtls_sha256_update(&s, b, l);
        }
        fclose(f);
        if (!w->partir) break;
    }
    mbedtls_sha256_finish(&s, h);
    mbedtls_sha256_free(&s);
    free(b);
    for (int i = 0; i < 32; i++) snprintf(hex + i * 2, 3, "%02x", h[i]);
    return 0;
}

static int procesar(Conexion *c, const Juego *j, int reemplazar, char *aviso, size_t n) {
    char api[400], sis[40], fich[700], url[1600], tmp[480];
    url_api(base_de(j), api, sizeof api);
    url_codificar(j->sistema, sis, sizeof sis);
    url_codificar(j->fichero, fich, sizeof fich);
    carpeta_temporal(j, tmp, sizeof tmp);
    crear_carpetas(tmp);
    const char *clave = clave_de(j);

    /* Ficheros que hay que bajar: [nombre, tamaño]. */
    struct { char nombre[204]; long long tam; } ficheros[16];
    int nf = 0;
    if (j->es_grupo) {
        snprintf(url, sizeof url, "%s?accion=grupo_ficheros&system=%s&grupo=%s", api, sis, fich);
        char *datos;
        size_t l;
        int e = http_memoria(c, "GET", url, clave, NULL, &datos, &l, 1 << 20, aviso, n);
        if (e != 200) {
            free(datos);
            if (e == 401) snprintf(aviso, n, "Esta librería necesita acceso: pide acceso en el menú (botón 1).");
            return -1;
        }
        json_t *lista = json_loads(datos, 0, NULL), *o;
        free(datos);
        size_t i;
        json_array_foreach(lista, i, o) {
            const char *nom = json_string_value(json_object_get(o, "nombre"));
            if (!nom || strchr(nom, '/') || strchr(nom, '\\') || !strcmp(nom, "..") || nf == 16) {
                json_decref(lista);
                snprintf(aviso, n, "La librería ha devuelto un fichero de grupo no válido.");
                return -1;
            }
            if (!strcmp(nom, "grupo.json")) continue;
            snprintf(ficheros[nf].nombre, sizeof ficheros[0].nombre, "%s", nom);
            ficheros[nf].tam = json_integer_value(json_object_get(o, "tamano"));
            nf++;
        }
        json_decref(lista);
        if (!nf) {
            snprintf(aviso, n, "El juego no tiene ficheros.");
            return -1;
        }
    } else {
        snprintf(ficheros[0].nombre, sizeof ficheros[0].nombre, "%s", j->fichero);
        ficheros[0].tam = j->tamano;
        nf = 1;
    }
    long long total = 0, antes = 0;
    for (int i = 0; i < nf; i++) total += ficheros[i].tam;

    for (int i = 0; i < nf; i++) {
        Escritor w = {0};
        snprintf(w.base, sizeof w.base, "%s/%s", tmp, ficheros[i].nombre);
        w.partir = !strcmp(j->sistema, "wii") && !j->es_grupo && !strcasecmp(extension(ficheros[i].nombre), ".wbfs");
        if (existe(w.base) && tamano_de(w.base) == ficheros[i].tam) {       /* ya bajado otra vez */
            antes += ficheros[i].tam;
            continue;
        }
        if (j->es_grupo) {
            char g[700];
            url_codificar(ficheros[i].nombre, g, sizeof g);
            snprintf(url, sizeof url, "%s?accion=grupo_fichero&system=%s&grupo=%s&fichero=%s", api, sis, fich, g);
        } else {
            snprintf(url, sizeof url, "%s?accion=descargar&system=%s&filename=%s", api, sis, fich);
        }
        int r = 1;
        if (!j->es_grupo && j->fuente_externa[0]) {
            /* Primero de la web del autor; vale solo si es exactamente el mismo fichero. */
            char hex[65];
            r = descargar(c, j->fuente_externa, NULL, &w, antes, total, aviso, n);
            if (r == 0) {
                SDL_LockMutex(cerrojo);
                snprintf(estado.nombre, sizeof estado.nombre, "%s (comprobando)", j->nombre);
                SDL_UnlockMutex(cerrojo);
                r = sha_descargado(&w, hex);
                SDL_LockMutex(cerrojo);
                snprintf(estado.nombre, sizeof estado.nombre, "%s", j->nombre);
                SDL_UnlockMutex(cerrojo);
                if (r == 0 && strcmp(hex, j->sha_enlace)) r = 1;
            }
            if (r && r != -2) escritor_borrar(&w);      /* a la copia de la librería */
        }
        if (r && r != -2) r = descargar(c, url, clave, &w, antes, total, aviso, n);
        if (r == -2) {
            borrar_recursivo(tmp);
            return -2;
        }
        if (r) return -1;
        if (escritor_terminar(&w) < 0) {
            snprintf(aviso, n, "No se pudo renombrar lo descargado.");
            return -1;
        }
        antes += ficheros[i].tam;
    }

    /* Actualización: lo descargado está entero; ahora se quita la versión anterior. */
    if (reemplazar) {
        char viejo[400];
        instalar_donde(j, viejo, sizeof viejo);
        if (viejo[0] && ruta_permitida(j, viejo)) borrar_recursivo(viejo);
    }

    /* A su sitio. */
    char destino[480], primero[480];
    snprintf(primero, sizeof primero, "%s/%s", tmp, ficheros[0].nombre);
    if (es_disco_nintendo(j->sistema)) {
        char id[7], titulo[160];
        /* En un grupo de Wii el .wbfs es el que lleva la cabecera. */
        for (int i = 0; i < nf; i++)
            if (!strcasecmp(extension(ficheros[i].nombre), ".wbfs")) snprintf(primero, sizeof primero, "%s/%s", tmp, ficheros[i].nombre);
        if (leer_id(primero, id)) {
            snprintf(aviso, n, "El fichero descargado no es un disco de %s.", nombre_sistema(j->sistema));
            borrar_recursivo(tmp);
            return -1;
        }
        nombre_fat(j->titulo[0] ? j->titulo : j->nombre, titulo, 120);
        snprintf(destino, sizeof destino, "%s/%s/%s [%s]", instalar_raiz(j->sistema),
                 !strcmp(j->sistema, "wii") ? "wbfs" : "games", titulo, id);
        if (existe(destino)) {
            /* Ya estaba en el disco (copiado a mano o con otra app): vale ese. */
            borrar_recursivo(tmp);
            SDL_LockMutex(cerrojo);
            indice_poner(j->sistema, j->fichero, destino, j->sha, j->tamano);
            SDL_UnlockMutex(cerrojo);
            snprintf(aviso, n, "%s ya estaba en el disco.", j->nombre);
            return 0;
        }
        crear_carpetas(destino);
        DIR *d = opendir(tmp);
        struct dirent *e;
        char de[600], a[600];
        int error = !d;
        while (d && (e = readdir(d))) {
            if (e->d_name[0] == '.') continue;
            const char *ext = extension(e->d_name);
            if (!strcmp(j->sistema, "gc")) snprintf(a, sizeof a, "%s/game.iso", destino);
            else snprintf(a, sizeof a, "%s/%s%s", destino, id, ext);
            for (char *p = a + strlen(destino); *p; p++) *p = (char)tolower((unsigned char)*p);
            if (!strcmp(j->sistema, "wii")) memcpy(a + strlen(destino) + 1, id, 6);    /* el ID en mayúsculas */
            snprintf(de, sizeof de, "%s/%s", tmp, e->d_name);
            if (rename(de, a)) error = 1;
        }
        if (d) closedir(d);
        if (error) {
            snprintf(aviso, n, "No se pudo mover el juego a %s.", destino);
            return -1;
        }
    } else {
        char carpeta[400];
        carpeta_roms(j->sistema, carpeta, sizeof carpeta);
        crear_carpetas(carpeta);
        if (j->es_grupo) {
            char stem[204];
            sin_extension(j->fichero, stem, sizeof stem);
            snprintf(destino, sizeof destino, "%s/%s", carpeta, stem);
            borrar_recursivo(destino);
            if (rename(tmp, destino)) {
                snprintf(aviso, n, "No se pudo mover el juego a %s.", destino);
                return -1;
            }
        } else {
            snprintf(destino, sizeof destino, "%s/%s", carpeta, j->fichero);
            remove(destino);
            if (rename(primero, destino)) {
                snprintf(aviso, n, "No se pudo mover el juego a %s.", destino);
                return -1;
            }
        }
    }
    borrar_recursivo(tmp);
    SDL_LockMutex(cerrojo);
    indice_poner(j->sistema, j->fichero, destino, j->sha, j->tamano);
    SDL_UnlockMutex(cerrojo);
    snprintf(aviso, n, "%s: %s", reemplazar ? "Actualizado" : "Instalado", j->nombre);
    return 0;
}

static int bucle(void *nada) {
    (void)nada;
    Conexion *c = con_nueva();
    SDL_LockMutex(cerrojo);
    while (!parar) {
        if (!n_cola) {
            SDL_CondWaitTimeout(aviso_cola, cerrojo, 500);
            continue;
        }
        actual = cola[0];
        memmove(cola, cola + 1, sizeof(Trabajo) * (size_t)--n_cola);
        hay_actual = 1;
        cancelar_actual = 0;
        estado.activa = 1;
        estado.hecho = 0;
        estado.total = actual.j.tamano;
        estado.velocidad = 0;
        estado.en_cola = n_cola;
        snprintf(estado.sistema, sizeof estado.sistema, "%s", actual.j.sistema);
        snprintf(estado.fichero, sizeof estado.fichero, "%s", actual.j.fichero);
        snprintf(estado.nombre, sizeof estado.nombre, "%s", actual.j.nombre);
        SDL_UnlockMutex(cerrojo);

        char aviso[400];
        int r = procesar(c, &actual.j, actual.reemplazar, aviso, sizeof aviso);

        SDL_LockMutex(cerrojo);
        hay_actual = 0;
        estado.activa = 0;
        estado.en_cola = n_cola;
        if (r == -2) snprintf(estado.aviso, sizeof estado.aviso, "Cancelado: %s", actual.j.nombre);
        else if (r) snprintf(estado.aviso, sizeof estado.aviso, "%s: %s", actual.j.nombre, aviso);
        else snprintf(estado.aviso, sizeof estado.aviso, "%s", aviso);
        estado.aviso_es_error = r != 0 && r != -2;
        estado.cambios++;
    }
    SDL_UnlockMutex(cerrojo);
    con_liberar(c);
    return 0;
}

void instalar_iniciar(void) {
    cerrojo = SDL_CreateMutex();
    aviso_cola = SDL_CreateCond();
    hilo = SDL_CreateThread(bucle, "descargas", NULL);
}

void instalar_parar(void) {
    if (!hilo) return;
    parar = 1;
    SDL_CondSignal(aviso_cola);
    SDL_WaitThread(hilo, NULL);
    hilo = NULL;
}

static int mismo(const Juego *a, const Juego *b) { return !strcmp(a->sistema, b->sistema) && !strcmp(a->fichero, b->fichero) && !strcmp(a->origen_url, b->origen_url); }

int instalar_en_cola(const Juego *j) {
    int r = 0;
    SDL_LockMutex(cerrojo);
    if (hay_actual && mismo(&actual.j, j)) r = 1;
    for (int i = 0; i < n_cola && !r; i++) r = mismo(&cola[i].j, j);
    SDL_UnlockMutex(cerrojo);
    return r;
}

double instalar_progreso(const Juego *j) {
    double r = -1;
    SDL_LockMutex(cerrojo);
    if (hay_actual && mismo(&actual.j, j)) r = estado.total > 0 ? (double)estado.hecho / (double)estado.total : 0;
    SDL_UnlockMutex(cerrojo);
    return r;
}

static int encolar(const Juego *j, int reemplazar, char *error, size_t n) {
    if (!instalar_compatible(j, error, n)) return -1;
    if (!j->descargable) {
        snprintf(error, n, "La librería no sirve este juego: está confirmado como corrupto.");
        return -1;
    }
    const char *raiz = instalar_raiz(j->sistema);
    long long libre = plat_libre(raiz);
    long long falta = j->tamano + MARGEN;
    /* Lo de esta misma descarga hecho otra vez ya ocupa sitio. */
    char tmp[480];
    carpeta_temporal(j, tmp, sizeof tmp);
    long long hecho = 0;
    DIR *d = opendir(tmp);
    struct dirent *e;
    char r[600];
    while (d && (e = readdir(d))) {
        snprintf(r, sizeof r, "%s/%s", tmp, e->d_name);
        long long t = tamano_de(r);
        if (e->d_name[0] != '.' && t > 0) hecho += t;
    }
    if (d) closedir(d);
    /* Lo que ya está en la cola también va a ocupar. */
    SDL_LockMutex(cerrojo);
    for (int i = 0; i < n_cola; i++)
        if (!strcmp(instalar_raiz(cola[i].j.sistema), raiz)) falta += cola[i].j.tamano;
    if (hay_actual && !strcmp(instalar_raiz(actual.j.sistema), raiz)) falta += estado.total - estado.hecho;
    SDL_UnlockMutex(cerrojo);
    if (libre >= 0 && libre + hecho < falta) {
        char a[32], b[32];
        texto_tamano(falta - hecho, a, sizeof a);
        texto_tamano(libre, b, sizeof b);
        snprintf(error, n, "No cabe: hacen falta %s y en %s quedan %s.", a, raiz, b);
        return -1;
    }
    SDL_LockMutex(cerrojo);
    int ya = hay_actual && mismo(&actual.j, j);
    for (int i = 0; i < n_cola && !ya; i++) ya = mismo(&cola[i].j, j);
    if (!ya && n_cola < MAX_COLA) {
        cola[n_cola].j = *j;
        cola[n_cola++].reemplazar = reemplazar;
        estado.en_cola = n_cola;
        SDL_CondSignal(aviso_cola);
    }
    SDL_UnlockMutex(cerrojo);
    return 0;
}

int instalar_encolar(const Juego *j, char *error, size_t n) { return encolar(j, 0, error, n); }
int instalar_actualizar(const Juego *j, char *error, size_t n) { return encolar(j, 1, error, n); }

void instalar_cancelar(const Juego *j) {
    SDL_LockMutex(cerrojo);
    if (hay_actual && mismo(&actual.j, j)) cancelar_actual = 1;
    for (int i = 0; i < n_cola; i++)
        if (mismo(&cola[i].j, j)) {
            memmove(cola + i, cola + i + 1, sizeof(Trabajo) * (size_t)(n_cola - i - 1));
            n_cola--;
            break;
        }
    estado.en_cola = n_cola;
    SDL_UnlockMutex(cerrojo);
}

/* Solo se borra dentro de las carpetas de juegos: nunca otra cosa del disco. */
static int ruta_permitida(const Juego *j, const char *donde) {
    const char *raiz = instalar_raiz(j->sistema);
    char permitida[400];
    if (!raiz) return 0;
    if (es_disco_nintendo(j->sistema)) snprintf(permitida, sizeof permitida, "%s/%s/", raiz, !strcmp(j->sistema, "wii") ? "wbfs" : "games");
    else {
        carpeta_roms(j->sistema, permitida, sizeof permitida - 1);
        strcat(permitida, "/");
    }
    return !strncmp(donde, permitida, strlen(permitida)) && !strstr(donde, "/../") && strlen(donde) > strlen(permitida);
}

int instalar_borrar(const Juego *j, char *error, size_t n) {
    char donde[400];
    instalar_donde(j, donde, sizeof donde);
    if (!donde[0]) {
        snprintf(error, n, "No está instalado.");
        return -1;
    }
    if (!ruta_permitida(j, donde)) {
        snprintf(error, n, "No se borra %s: está fuera de la carpeta de juegos.", donde);
        return -1;
    }
    if (borrar_recursivo(donde)) {
        snprintf(error, n, "No se pudo borrar todo %s.", donde);
        return -1;
    }
    SDL_LockMutex(cerrojo);
    indice_quitar(j->sistema, j->fichero);
    estado.cambios++;
    SDL_UnlockMutex(cerrojo);
    return 0;
}

void instalar_estado(EstadoDescargas *e) {
    SDL_LockMutex(cerrojo);
    *e = estado;
    SDL_UnlockMutex(cerrojo);
}

static long long tamano_carpeta(const char *ruta) {
    DIR *d = opendir(ruta);
    if (!d) return 0;
    long long t = 0;
    struct dirent *e;
    char h[600];
    struct stat s;
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        snprintf(h, sizeof h, "%s/%s", ruta, e->d_name);
        if (stat(h, &s)) continue;
        t += S_ISDIR(s.st_mode) ? tamano_carpeta(h) : (long long)s.st_size;
    }
    closedir(d);
    return t;
}

long long instalar_restos(void) {
    long long t = 0;
    const char *raices[] = {plat_raiz("usb"), plat_raiz("sd")};
    char r[300];
    for (int i = 0; i < 2; i++) {
        if (!raices[i]) continue;
        snprintf(r, sizeof r, "%s/romslibrary-descargas", raices[i]);
        t += tamano_carpeta(r);
    }
    return t;
}

void instalar_borrar_restos(void) {
    const char *raices[] = {plat_raiz("usb"), plat_raiz("sd")};
    char r[300];
    SDL_LockMutex(cerrojo);
    int ocupado = hay_actual || n_cola;
    SDL_UnlockMutex(cerrojo);
    if (ocupado) return;                /* no se borra lo que se está bajando */
    for (int i = 0; i < 2; i++) {
        if (!raices[i]) continue;
        snprintf(r, sizeof r, "%s/romslibrary-descargas", raices[i]);
        borrar_recursivo(r);
    }
}
