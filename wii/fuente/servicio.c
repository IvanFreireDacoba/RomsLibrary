#include "servicio.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include <jansson.h>
#include <mbedtls/sha256.h>

#include "ajustes.h"
#include "catalogo.h"
#include "plataforma.h"
#include "version.h"

/* ---------------- acceso ---------------- */

int acceso_solicitar(Conexion *c, char *error, size_t n) {
    unsigned char azar[24];
    char token[49], nombre[64], api[400], url[500];
    plat_entropia(azar, sizeof azar);
    for (int i = 0; i < 24; i++) snprintf(token + i * 2, 3, "%02x", azar[i]);
    plat_nombre_consola(nombre, sizeof nombre);
    json_t *cuerpo = json_pack("{s:s, s:s, s:s}", "token", token, "nombre", nombre, "consola", "");
    char *texto = json_dumps(cuerpo, JSON_COMPACT);
    json_decref(cuerpo);
    url_api(ajuste("url"), api, sizeof api);
    snprintf(url, sizeof url, "%s?accion=solicitar_acceso", api);
    char *datos;
    size_t l;
    int estado = http_memoria(c, "POST", url, NULL, texto, &datos, &l, 65536, error, n);
    free(texto);
    free(datos);
    if (estado != 200) return -1;
    ajuste_poner("acceso_pendiente", token);
    ajustes_guardar();
    return 0;
}

int acceso_comprobar(Conexion *c) {
    const char *token = ajuste("acceso_pendiente");
    if (!*token) return 0;
    char api[400], url[500], error[200], *datos;
    size_t l;
    url_api(ajuste("url"), api, sizeof api);
    snprintf(url, sizeof url, "%s?accion=acceso_estado", api);
    if (http_memoria(c, "GET", url, token, NULL, &datos, &l, 65536, error, sizeof error) != 200) {
        free(datos);
        return 0;
    }
    json_t *j = json_loads(datos, 0, NULL);
    free(datos);
    const char *e = json_string_value(json_object_get(j, "estado"));
    int r = 0;
    if (e && !strcmp(e, "aceptada")) {
        char t[80];
        snprintf(t, sizeof t, "%s", token);
        ajuste_poner("clave", t);
        ajuste_poner("acceso_pendiente", "");
        r = 1;
    } else if (e && strcmp(e, "pendiente")) {          /* rechazada, o revocada/caducada */
        ajuste_poner("acceso_pendiente", "");
        r = -1;
    }
    json_decref(j);
    if (r) ajustes_guardar();
    return r;
}

/* ---------------- actualización ---------------- */

static json_t *info_publicada(Conexion *c) {
    char url[300], error[200], *datos;
    size_t l;
    snprintf(url, sizeof url, "%s/APIs/RomLibraryApp?accion=wii_version", ORIGEN_ACTUALIZACIONES);
    if (http_memoria(c, "GET", url, NULL, NULL, &datos, &l, 65536, error, sizeof error) != 200) {
        free(datos);
        return NULL;
    }
    json_t *j = json_loads(datos, 0, NULL);
    free(datos);
    return j;
}

static int comparar_versiones(const char *a, const char *b) {
    for (;;) {
        long x = strtol(a, (char **)&a, 10), y = strtol(b, (char **)&b, 10);
        if (x != y) return x < y ? -1 : 1;
        if (*a != '.' && *b != '.') return 0;
        if (*a == '.') a++;
        if (*b == '.') b++;
    }
}

int canal_version_publicada = -1;

static int version_canal(const char *v) {
    if (!v) return -1;
    char *fin;
    long mayor = strtol(v, &fin, 10), menor = *fin == '.' ? strtol(fin + 1, NULL, 10) : 0;
    return (int)(mayor << 8 | (menor & 0xff));
}

int actualizacion_buscar(Conexion *c, char *version, size_t n) {
    json_t *j = info_publicada(c);
    canal_version_publicada = version_canal(json_string_value(json_object_get(json_object_get(j, "canal"), "version")));
    const char *v = json_string_value(json_object_get(j, "version"));
    int r = !v ? -1 : comparar_versiones(v, RL_VERSION) > 0;
    if (r > 0) snprintf(version, n, "%s", v);
    json_decref(j);
    return r;
}

static void sha256_hex(const unsigned char *d, size_t l, char hex[65]) {
    unsigned char h[32];
    mbedtls_sha256(d, l, h, 0);
    for (int i = 0; i < 32; i++) snprintf(hex + i * 2, 3, "%02x", h[i]);
}

int canal_descargar(Conexion *c, unsigned char **wad, size_t *tam, char *error, size_t n) {
    *wad = NULL;
    json_t *j = info_publicada(c), *canal = json_object_get(j, "canal");
    const char *fichero = json_string_value(json_object_get(canal, "fichero"));
    const char *sha = json_string_value(json_object_get(canal, "sha256"));
    if (!fichero || !sha || strchr(fichero, '/')) {
        json_decref(j);
        snprintf(error, n, "sund.es no publica el canal ahora mismo.");
        return -1;
    }
    char url[300], hex[65], *datos;
    snprintf(url, sizeof url, "%s/APIs/RomLibraryApp?accion=wii&fichero=%s", ORIGEN_ACTUALIZACIONES, fichero);
    int r = http_memoria(c, "GET", url, NULL, NULL, &datos, tam, 8u << 20, error, n);
    if (r == 200) {
        sha256_hex((unsigned char *)datos, *tam, hex);
        if (strcmp(hex, sha)) {
            snprintf(error, n, "El canal descargado no coincide con el publicado.");
            r = -1;
        }
    }
    json_decref(j);
    if (r != 200) {
        free(datos);
        return -1;
    }
    *wad = (unsigned char *)datos;
    return 0;
}

static int sha256_fichero(const char *ruta, char hex[65]) {
    FILE *f = fopen(ruta, "rb");
    if (!f) return -1;
    mbedtls_sha256_context s;
    mbedtls_sha256_init(&s);
    mbedtls_sha256_starts(&s, 0);
    unsigned char b[16384], h[32];
    size_t l;
    while ((l = fread(b, 1, sizeof b, f)) > 0) mbedtls_sha256_update(&s, b, l);
    fclose(f);
    mbedtls_sha256_finish(&s, h);
    mbedtls_sha256_free(&s);
    for (int i = 0; i < 32; i++) snprintf(hex + i * 2, 3, "%02x", h[i]);
    return 0;
}

int actualizacion_instalar(Conexion *c, char *error, size_t n) {
    json_t *j = info_publicada(c), *ficheros = json_object_get(j, "ficheros"), *datos;
    if (!json_is_object(ficheros)) {
        json_decref(j);
        snprintf(error, n, "No se pudo consultar la versión publicada.");
        return -1;
    }
    const char *nombre;
    char ruta[400], url[400];
    int fallo = 0;
    /* 1) Bajar y comprobar cada fichero junto al actual («.nuevo»). */
    json_object_foreach(ficheros, nombre, datos) {
        const char *sha = json_string_value(json_object_get(datos, "sha256"));
        if (!sha || strchr(nombre, '/') || strchr(nombre, '\\') || nombre[0] == '.') {
            fallo = 1;
            break;
        }
        snprintf(ruta, sizeof ruta, "%s/%s.nuevo", plat_carpeta_app(), nombre);
        snprintf(url, sizeof url, "%s/APIs/RomLibraryApp?accion=wii&fichero=%s", ORIGEN_ACTUALIZACIONES, nombre);
        char *contenido;
        size_t l;
        if (http_memoria(c, "GET", url, NULL, NULL, &contenido, &l, 16u << 20, error, n) != 200) {
            free(contenido);
            fallo = 1;
            break;
        }
        FILE *f = fopen(ruta, "wb");
        int ok = f && fwrite(contenido, 1, l, f) == l;
        if (f && fclose(f)) ok = 0;
        free(contenido);
        char hex[65];
        if (!ok || sha256_fichero(ruta, hex) || strcmp(hex, sha)) {
            snprintf(error, n, ok ? "%s no coincide con el publicado." : "No se pudo guardar %s.", nombre);
            fallo = 1;
            break;
        }
    }
    /* 2) Cambiarlos (o borrar los .nuevo si algo falló). */
    json_object_foreach(ficheros, nombre, datos) {
        char final[400];
        snprintf(ruta, sizeof ruta, "%s/%s.nuevo", plat_carpeta_app(), nombre);
        snprintf(final, sizeof final, "%s/%s", plat_carpeta_app(), nombre);
        if (fallo) {
            remove(ruta);
            continue;
        }
        /* El actual se aparta antes, y vuelve si el cambio falla: nunca se queda sin boot.dol. */
        char anterior[420];
        snprintf(anterior, sizeof anterior, "%s.anterior", final);
        remove(anterior);
        struct stat s;
        int habia = stat(final, &s) == 0;
        if (habia && rename(final, anterior)) {
            remove(ruta);
            snprintf(error, n, "No se pudo sustituir %s.", nombre);
            fallo = 1;
            continue;
        }
        if (rename(ruta, final)) {
            if (habia) rename(anterior, final);
            snprintf(error, n, "No se pudo sustituir %s.", nombre);
            fallo = 1;
            continue;
        }
        remove(anterior);
    }
    json_decref(j);
    return fallo ? -1 : 0;
}
