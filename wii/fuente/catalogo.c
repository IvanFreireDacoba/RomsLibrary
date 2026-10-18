#include "catalogo.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <jansson.h>

#include "ajustes.h"
#include "instalar.h"

const Sistema SISTEMAS[] = {
    {"wii", "Wii"},
    {"gc", "GameCube"},
    {"gba", "Game Boy Advance"},
    {"gbc", "Game Boy Color"},
    {"gb", "Game Boy"},
    {"n64", "Nintendo 64"},
    {"psx", "PlayStation"},
};
const int N_SISTEMAS = sizeof SISTEMAS / sizeof SISTEMAS[0];

const char *nombre_sistema(const char *id) {
    for (int i = 0; i < N_SISTEMAS; i++)
        if (!strcmp(SISTEMAS[i].id, id)) return SISTEMAS[i].nombre;
    return id;
}

void url_api(const char *base, char *salida, size_t n) {
    size_t l = strlen(base);
    while (l && base[l - 1] == '/') l--;
    snprintf(salida, n, "%.*s/APIs/RomLibraryApp", (int)l, base);
}

const char *base_de(const Juego *j) { return j->origen_url[0] ? j->origen_url : ajuste("url"); }
const char *clave_de(const Juego *j) { return j->origen_url[0] ? NULL : ajuste("clave"); }

/* Un nombre que acaba en disco tal cual: sin rutas ni trucos (como la app de Android). */
static int componente_seguro(const char *s) {
    size_t l = s ? strlen(s) : 0;
    if (!l || l > 200 || !strcmp(s, ".") || !strcmp(s, "..")) return 0;
    if (s[0] == ' ' || s[l - 1] == ' ' || s[l - 1] == '.') return 0;
    for (const unsigned char *p = (const unsigned char *)s; *p; p++)
        if (*p < 32 || strchr("/\\:*?\"<>|", *p)) return 0;
    return 1;
}

static int enlace_https(const char *u) {
    return u && !strncmp(u, "https://", 8) && !strchr(u, '@') && strlen(u) < 290;
}

static const char *texto(json_t *o, const char *k) {
    const char *v = json_string_value(json_object_get(o, k));
    return v ? v : "";
}

static void sin_extension(const char *fichero, char *salida, size_t n) {
    snprintf(salida, n, "%s", fichero);
    char *punto = strrchr(salida, '.');
    if (punto && punto != salida && strlen(punto) <= 5) *punto = 0;
}

static int preferencia(const Juego *j) {
    int es = 0, en = 0;
    for (int i = 0; i < j->n_idiomas; i++) {
        if (!strcmp(j->idiomas[i], "es")) es = 1;
        if (!strcmp(j->idiomas[i], "en")) en = 1;
    }
    int idioma = es ? 0 : en ? 1 : j->n_idiomas ? 2 : 3;
    return (j->descargable ? 0 : 20) + (j->corrupto ? 10 : 0) + idioma;
}

int catalogo_cargar(Conexion *c, Catalogo *cat, char *error, size_t n) {
    char base[400], url[700];
    memset(cat, 0, sizeof *cat);
    url_api(ajuste("url"), base, sizeof base);
    snprintf(url, sizeof url, "%s?accion=juegos&libreria=personal", base);
    char *datos;
    size_t l;
    int estado = http_memoria(c, "GET", url, ajuste("clave"), NULL, &datos, &l, 32u << 20, error, n);
    if (estado < 0) return -1;
    if (estado != 200) {
        free(datos);
        return -estado;
    }
    json_error_t je;
    json_t *lista = json_loadb(datos, l, 0, &je);
    free(datos);
    if (!json_is_array(lista)) {
        json_decref(lista);
        snprintf(error, n, "La librería ha respondido algo que no es un catálogo.");
        return -1;
    }
    cat->juegos = calloc(json_array_size(lista) + 1, sizeof(Juego));
    if (!cat->juegos) {
        json_decref(lista);
        snprintf(error, n, "Sin memoria para el catálogo.");
        return -1;
    }
    size_t i;
    json_t *o;
    json_array_foreach(lista, i, o) {
        if (!json_is_object(o)) continue;
        const char *sistema = texto(o, "system"), *fichero = texto(o, "filename");
        int valido = 0;
        for (int s = 0; s < N_SISTEMAS; s++) valido |= !strcmp(SISTEMAS[s].id, sistema);
        if (!valido || !componente_seguro(fichero)) continue;
        Juego *j = &cat->juegos[cat->n];
        memset(j, 0, sizeof *j);
        snprintf(j->sistema, sizeof j->sistema, "%s", sistema);
        snprintf(j->fichero, sizeof j->fichero, "%s", fichero);
        sin_extension(fichero, j->nombre, sizeof j->nombre);
        json_t *t = json_object_get(o, "sizeBytes");
        j->tamano = json_is_integer(t) ? json_integer_value(t) : json_is_real(t) ? (long long)json_real_value(t) : 0;
        j->es_grupo = json_is_true(json_object_get(o, "esGrupo"));
        const char *origen = texto(o, "origen");
        if (*origen) {
            const char *ou = texto(o, "origenUrl");
            if (!enlace_https(ou)) continue;
            snprintf(j->origen_url, sizeof j->origen_url, "%s", ou);
            size_t lo = strlen(j->origen_url);
            while (lo && j->origen_url[lo - 1] == '/') j->origen_url[--lo] = 0;
        }
        const char *portada = texto(o, "coverUrl");
        if (j->origen_url[0] && !strncmp(portada, j->origen_url, strlen(j->origen_url)) &&
            portada[strlen(j->origen_url)] == '/' && enlace_https(portada) && strlen(portada) < sizeof j->portada)
            snprintf(j->portada, sizeof j->portada, "%s", portada);
        else if (portada[0] == '/') {
            const char *b = ajuste("url");
            size_t lb = strlen(b);
            while (lb && b[lb - 1] == '/') lb--;
            snprintf(j->portada, sizeof j->portada, "%.*s%s", (int)lb, b, portada);
        }
        snprintf(j->titulo, sizeof j->titulo, "%s", texto(o, "titulo"));
        json_t *idiomas = json_object_get(o, "idiomas"), *id;
        size_t k;
        json_array_foreach(idiomas, k, id) {
            const char *s = json_string_value(id);
            if (s && strlen(s) == 2 && islower((unsigned char)s[0]) && islower((unsigned char)s[1]) &&
                j->n_idiomas < MAX_IDIOMAS)
                snprintf(j->idiomas[j->n_idiomas++], 3, "%s", s);
        }
        j->corrupto = json_is_true(json_object_get(o, "corrupto"));
        j->corrupto_automatico = !json_is_false(json_object_get(o, "corruptoAutomatico"));
        j->descargable = !json_is_false(json_object_get(o, "descargable"));
        snprintf(j->motivo_corrupto, sizeof j->motivo_corrupto, "%s", texto(o, "motivoCorrupto"));
        const char *sha = texto(o, "sha");
        if (strlen(sha) == 64 && strspn(sha, "0123456789abcdef") == 64) snprintf(j->sha, sizeof j->sha, "%s", sha);
        /* Enlace del autor: la app descarga de su web (sus descargas y métricas se quedan allí). */
        const char *fe = texto(o, "fuenteExterna"), *se = texto(o, "shaEnlace");
        if (j->sha[0] && enlace_https(fe)) {
            snprintf(j->fuente_externa, sizeof j->fuente_externa, "%s", fe);
            snprintf(j->sha_enlace, sizeof j->sha_enlace, "%s",
                     strlen(se) == 64 && strspn(se, "0123456789abcdef") == 64 ? se : j->sha);
        }
        cat->n++;
    }
    json_decref(lista);
    /* Grupos de idiomas: mismo título (o mismo fichero sin título), consola y librería. */
    for (int a = 0; a < cat->n; a++) {
        Juego *ja = &cat->juegos[a];
        ja->grupo = a;
        for (int b = 0; b < a; b++) {
            Juego *jb = &cat->juegos[b];
            if (strcmp(ja->sistema, jb->sistema) || strcmp(ja->origen_url, jb->origen_url)) continue;
            if (ja->titulo[0] ? !strcasecmp(ja->titulo, jb->titulo) : (!jb->titulo[0] && !strcmp(ja->fichero, jb->fichero))) {
                ja->grupo = jb->grupo;
                break;
            }
        }
    }
    for (int a = 0; a < cat->n; a++) {
        cat->juegos[a].instalado = instalar_esta_instalado(&cat->juegos[a]);
        cat->juegos[a].actualizable = cat->juegos[a].instalado && instalar_hay_version_nueva(&cat->juegos[a]);
    }
    return 0;
}

void catalogo_liberar(Catalogo *cat) {
    free(cat->juegos);
    memset(cat, 0, sizeof *cat);
}

int catalogo_variantes(const Catalogo *cat, int i, int **salida) {
    int *v = malloc(sizeof(int) * (size_t)(cat->n + 1)), n = 0;
    if (!v) {
        *salida = NULL;
        return 0;
    }
    for (int a = 0; a < cat->n; a++)
        if (cat->juegos[a].grupo == cat->juegos[i].grupo) v[n++] = a;
    /* Orden por preferencia (estable). */
    for (int a = 1; a < n; a++)
        for (int b = a; b > 0 && preferencia(&cat->juegos[v[b]]) < preferencia(&cat->juegos[v[b - 1]]); b--) {
            int t = v[b];
            v[b] = v[b - 1];
            v[b - 1] = t;
        }
    *salida = v;
    return n;
}

int catalogo_entradas(const Catalogo *cat, const char *sistema, int solo_instalados, int **salida) {
    int *e = malloc(sizeof(int) * (size_t)(cat->n + 1)), n = 0;
    if (!e) {
        *salida = NULL;
        return 0;
    }
    for (int a = 0; a < cat->n; a++) {
        const Juego *j = &cat->juegos[a];
        if (strcmp(j->sistema, sistema) || j->grupo != a) continue;     /* uno por grupo: el primero */
        int *v, nv = catalogo_variantes(cat, a, &v), elegido = nv ? v[0] : a, alguno = 0;
        for (int k = 0; k < nv; k++)
            if (cat->juegos[v[k]].instalado || instalar_en_cola(&cat->juegos[v[k]])) {
                elegido = v[k];
                alguno = 1;
                break;
            }
        free(v);
        if (solo_instalados && !alguno) continue;
        e[n++] = elegido;
    }
    *salida = e;
    return n;
}

void etiqueta_idiomas(const Juego *j, char *salida, size_t n) {
    size_t l = 0;
    salida[0] = 0;
    for (int i = 0; i < j->n_idiomas && l + 4 < n; i++)
        l += (size_t)snprintf(salida + l, n - l, "%s%c%c", i ? " " : "", toupper((unsigned char)j->idiomas[i][0]),
                              toupper((unsigned char)j->idiomas[i][1]));
}

void texto_tamano(long long b, char *salida, size_t n) {
    double mb = (double)b / 1048576.0;
    if (mb >= 1024) snprintf(salida, n, "%.1f GB", mb / 1024);
    else if (mb >= 1) snprintf(salida, n, "%.0f MB", mb);
    else snprintf(salida, n, "%lld KB", b / 1024);
}
