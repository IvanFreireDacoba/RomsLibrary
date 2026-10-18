#include "red.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <jansson.h>
#include <mbedtls/ctr_drbg.h>
#include <mbedtls/entropy.h>
#include <mbedtls/error.h>
#include <mbedtls/platform_time.h>
#include <mbedtls/ssl.h>
#include <mbedtls/x509_crt.h>

#include "plataforma.h"
#include "recursos.h"
#include "version.h"

#define AGENTE "RomsLibrary-Wii/" RL_VERSION
#define ESPERA_MS 30000

static mbedtls_x509_crt raices;

struct Conexion {
    int s;
    int tls;
    char host[128];
    int puerto;
    int viva;                 /* conectada y reutilizable */
    mbedtls_ssl_context ssl;
    mbedtls_ssl_config conf;
    mbedtls_ctr_drbg_context drbg;
    mbedtls_entropy_context entropia;
    int ssl_listo;
    /* respuesta en curso */
    char buf[16384];
    int buf_ini, buf_fin;
    long long quedan;         /* bytes de cuerpo por leer (-1 = hasta cerrar) */
    int troceada;
    long long trozo_quedan;
    int fin_cuerpo;
    int cerrar_al_final;
};

/* mbedTLS (MBEDTLS_ENTROPY_HARDWARE_ALT) pide aquí su entropía. */
int mbedtls_hardware_poll(void *datos, unsigned char *salida, size_t n, size_t *len) {
    (void)datos;
    plat_entropia(salida, n);
    *len = n;
    return 0;
}

/* MBEDTLS_PLATFORM_MS_TIME_ALT: newlib en la Wii no da un reloj que mbedTLS reconozca. */
mbedtls_ms_time_t mbedtls_ms_time(void) { return (mbedtls_ms_time_t)plat_ms(); }

int red_iniciar(char *error, size_t n) {
    mbedtls_x509_crt_init(&raices);
    /* El paquete de Mozilla (PEM con '\0' al final, como pide mbedTLS). */
    int r = mbedtls_x509_crt_parse(&raices, (const unsigned char *)recurso_cacert, recurso_cacert_tam);
    if (r < 0) {
        snprintf(error, n, "Certificados raíz no válidos (%d).", r);
        return -1;
    }
    return 0;
}

Conexion *con_nueva(void) {
    Conexion *c = calloc(1, sizeof *c);
    if (c) c->s = -1;
    return c;
}

static void desconectar(Conexion *c) {
    if (c->ssl_listo) {
        if (c->viva) mbedtls_ssl_close_notify(&c->ssl);
        mbedtls_ssl_free(&c->ssl);
        mbedtls_ssl_config_free(&c->conf);
        mbedtls_ctr_drbg_free(&c->drbg);
        mbedtls_entropy_free(&c->entropia);
        c->ssl_listo = 0;
    }
    plat_cerrar(c->s);
    c->s = -1;
    c->viva = 0;
}

void con_liberar(Conexion *c) {
    if (!c) return;
    desconectar(c);
    free(c);
}

static int bio_enviar(void *ctx, const unsigned char *b, size_t n) {
    Conexion *c = ctx;
    int r = plat_enviar(c->s, b, (int)(n > 32768 ? 32768 : n));
    return r < 0 ? MBEDTLS_ERR_SSL_INTERNAL_ERROR : r;
}

static int bio_recibir(void *ctx, unsigned char *b, size_t n) {
    Conexion *c = ctx;
    int r = plat_recibir(c->s, b, (int)n, ESPERA_MS);
    if (r == -2) return MBEDTLS_ERR_SSL_TIMEOUT;
    if (r < 0) return MBEDTLS_ERR_SSL_INTERNAL_ERROR;
    if (r == 0) return MBEDTLS_ERR_SSL_PEER_CLOSE_NOTIFY;
    return r;
}

/* En una CPU sin AES por hardware ChaCha20 va más rápido: primero. */
static const int suites[] = {
    MBEDTLS_TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256,
    MBEDTLS_TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256,
    MBEDTLS_TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256,
    MBEDTLS_TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,
    MBEDTLS_TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384,
    MBEDTLS_TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,
    0};

static int conectar(Conexion *c, const char *host, int puerto, int tls, char *error, size_t n) {
    if (c->viva && c->tls == tls && c->puerto == puerto && !strcmp(c->host, host)) return 0;
    desconectar(c);
    c->s = plat_conectar(host, puerto, error, n);
    if (c->s < 0) return -1;
    snprintf(c->host, sizeof c->host, "%s", host);
    c->puerto = puerto;
    c->tls = tls;
    if (tls) {
        mbedtls_ssl_init(&c->ssl);
        mbedtls_ssl_config_init(&c->conf);
        mbedtls_ctr_drbg_init(&c->drbg);
        mbedtls_entropy_init(&c->entropia);
        c->ssl_listo = 1;
        int r = mbedtls_ctr_drbg_seed(&c->drbg, mbedtls_entropy_func, &c->entropia,
                                      (const unsigned char *)"RomsLibrary-Wii", 15);
        if (!r) r = mbedtls_ssl_config_defaults(&c->conf, MBEDTLS_SSL_IS_CLIENT, MBEDTLS_SSL_TRANSPORT_STREAM,
                                                MBEDTLS_SSL_PRESET_DEFAULT);
        if (!r) {
            mbedtls_ssl_conf_authmode(&c->conf, MBEDTLS_SSL_VERIFY_REQUIRED);
            mbedtls_ssl_conf_ca_chain(&c->conf, &raices, NULL);
            mbedtls_ssl_conf_rng(&c->conf, mbedtls_ctr_drbg_random, &c->drbg);
            mbedtls_ssl_conf_ciphersuites(&c->conf, suites);
            r = mbedtls_ssl_setup(&c->ssl, &c->conf);
        }
        if (!r) r = mbedtls_ssl_set_hostname(&c->ssl, host);
        if (!r) {
            mbedtls_ssl_set_bio(&c->ssl, c, bio_enviar, bio_recibir, NULL);
            while ((r = mbedtls_ssl_handshake(&c->ssl)) == MBEDTLS_ERR_SSL_WANT_READ || r == MBEDTLS_ERR_SSL_WANT_WRITE) {
            }
        }
        if (r) {
            char texto[100];
            mbedtls_strerror(r, texto, sizeof texto);
            uint32_t v = mbedtls_ssl_get_verify_result(&c->ssl);
            if (v && v != (uint32_t)-1) snprintf(error, n, "Certificado de %s no válido (%#x).", host, (unsigned)v);
            else snprintf(error, n, "TLS con %s: %s", host, texto);
            desconectar(c);
            return -1;
        }
    }
    c->viva = 1;
    return 0;
}

static int enviar_todo(Conexion *c, const char *datos, size_t n) {
    while (n) {
        int r = c->tls ? mbedtls_ssl_write(&c->ssl, (const unsigned char *)datos, n) : plat_enviar(c->s, datos, (int)n);
        if (c->tls && (r == MBEDTLS_ERR_SSL_WANT_READ || r == MBEDTLS_ERR_SSL_WANT_WRITE)) continue;
        if (r <= 0) return -1;
        datos += r;
        n -= (size_t)r;
    }
    return 0;
}

/* Lee del socket al búfer. >0 bytes, 0 cerrado, -1 error. */
static int rellenar(Conexion *c) {
    if (c->buf_ini == c->buf_fin) c->buf_ini = c->buf_fin = 0;
    if (c->buf_fin == (int)sizeof c->buf) return (int)sizeof c->buf;
    int r;
    if (c->tls) {
        do r = mbedtls_ssl_read(&c->ssl, (unsigned char *)c->buf + c->buf_fin, sizeof c->buf - (size_t)c->buf_fin);
        while (r == MBEDTLS_ERR_SSL_WANT_READ || r == MBEDTLS_ERR_SSL_WANT_WRITE);
        if (r == MBEDTLS_ERR_SSL_PEER_CLOSE_NOTIFY) r = 0;
    } else {
        r = plat_recibir(c->s, c->buf + c->buf_fin, (int)sizeof c->buf - c->buf_fin, ESPERA_MS);
        if (r == -2) r = -1;
    }
    if (r > 0) c->buf_fin += r;
    return r;
}

/* Una línea (sin \r\n) del búfer. 0 bien, -1 error. */
static int leer_linea(Conexion *c, char *linea, size_t n) {
    for (;;) {
        char *fin = memchr(c->buf + c->buf_ini, '\n', (size_t)(c->buf_fin - c->buf_ini));
        if (fin) {
            size_t l = (size_t)(fin - (c->buf + c->buf_ini));
            size_t copia = l < n - 1 ? l : n - 1;
            memcpy(linea, c->buf + c->buf_ini, copia);
            linea[copia] = 0;
            if (copia && linea[copia - 1] == '\r') linea[copia - 1] = 0;
            c->buf_ini += (int)l + 1;
            return 0;
        }
        if (c->buf_ini > 0) {
            memmove(c->buf, c->buf + c->buf_ini, (size_t)(c->buf_fin - c->buf_ini));
            c->buf_fin -= c->buf_ini;
            c->buf_ini = 0;
        }
        if (c->buf_fin == (int)sizeof c->buf) return -1;          /* línea demasiado larga */
        if (rellenar(c) <= 0) return -1;
    }
}

static int partir_url(const char *url, int *tls, char *host, size_t nh, int *puerto, const char **ruta) {
    const char *p;
    if (!strncmp(url, "https://", 8)) {
        *tls = 1;
        *puerto = 443;
        p = url + 8;
    } else if (!strncmp(url, "http://", 7)) {
        *tls = 0;
        *puerto = 80;
        p = url + 7;
    } else {
        return -1;
    }
    size_t l = strcspn(p, "/:?");
    if (!l || l >= nh) return -1;
    memcpy(host, p, l);
    host[l] = 0;
    p += l;
    if (*p == ':') {
        *puerto = atoi(p + 1);
        p += 1 + strspn(p + 1, "0123456789");
    }
    *ruta = *p ? p : "/";
    return 0;
}

int http_abrir(Conexion *c, const char *metodo, const char *url, const char *clave, long long desde,
               const char *cuerpo, Respuesta *r, char *error, size_t n) {
    int tls, puerto;
    char host[128];
    const char *ruta;
    memset(r, 0, sizeof *r);
    r->longitud = r->total = -1;
    if (partir_url(url, &tls, host, sizeof host, &puerto, &ruta)) {
        snprintf(error, n, "Dirección no válida: %.80s", url);
        return -1;
    }
    for (int intento = 0; intento < 2; intento++) {
        int reutilizada = c->viva && c->tls == tls && c->puerto == puerto && !strcmp(c->host, host);
        if (conectar(c, host, puerto, tls, error, n)) return -1;
        char cab[4096];
        int l = snprintf(cab, sizeof cab,
                         "%s %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: " AGENTE "\r\nAccept-Encoding: identity\r\n"
                         "Connection: keep-alive\r\n",
                         metodo, ruta, host);
        if (clave && *clave) l += snprintf(cab + l, sizeof cab - (size_t)l, "X-App-Token: %s\r\n", clave);
        if (desde > 0) l += snprintf(cab + l, sizeof cab - (size_t)l, "Range: bytes=%lld-\r\n", desde);
        if (cuerpo)
            l += snprintf(cab + l, sizeof cab - (size_t)l, "Content-Type: application/json\r\nContent-Length: %u\r\n",
                          (unsigned)strlen(cuerpo));
        l += snprintf(cab + l, sizeof cab - (size_t)l, "\r\n");
        if (l >= (int)sizeof cab) {
            snprintf(error, n, "Petición demasiado larga.");
            return -1;
        }
        c->buf_ini = c->buf_fin = 0;
        char linea[4096];
        if (enviar_todo(c, cab, (size_t)l) || (cuerpo && enviar_todo(c, cuerpo, strlen(cuerpo))) ||
            leer_linea(c, linea, sizeof linea)) {
            desconectar(c);
            if (reutilizada) continue;          /* el servidor cerró la conexión guardada: otra nueva */
            snprintf(error, n, "Se cortó la conexión con %s.", host);
            return -1;
        }
        if (strncmp(linea, "HTTP/1.", 7) || strlen(linea) < 12) {
            desconectar(c);
            snprintf(error, n, "Respuesta no válida de %s.", host);
            return -1;
        }
        r->estado = atoi(linea + 9);
        c->troceada = 0;
        c->cerrar_al_final = linea[7] == '0';
        c->quedan = -1;
        for (;;) {
            if (leer_linea(c, linea, sizeof linea)) {
                desconectar(c);
                snprintf(error, n, "Se cortó la conexión con %s.", host);
                return -1;
            }
            if (!linea[0]) break;
            char *dos = strchr(linea, ':');
            if (!dos) continue;
            *dos = 0;
            char *valor = dos + 1;
            while (*valor == ' ') valor++;
            for (char *q = linea; *q; q++) *q = (char)tolower((unsigned char)*q);
            if (!strcmp(linea, "content-length")) r->longitud = strtoll(valor, NULL, 10);
            else if (!strcmp(linea, "transfer-encoding") && strstr(valor, "chunked")) c->troceada = 1;
            else if (!strcmp(linea, "connection") && (strstr(valor, "close") || strstr(valor, "Close")))
                c->cerrar_al_final = 1;
            else if (!strcmp(linea, "location")) snprintf(r->ubicacion, sizeof r->ubicacion, "%s", valor);
            else if (!strcmp(linea, "content-range")) {
                char *barra = strchr(valor, '/');
                if (barra && barra[1] != '*') r->total = strtoll(barra + 1, NULL, 10);
            }
        }
        r->parcial = r->estado == 206;
        if (r->total < 0) r->total = r->longitud;
        if (!strcmp(metodo, "HEAD") || r->estado == 204 || r->estado == 304) c->quedan = 0;
        else if (!c->troceada) c->quedan = r->longitud;     /* -1: hasta que se cierre */
        if (c->quedan < 0 && !c->troceada) c->cerrar_al_final = 1;
        c->trozo_quedan = 0;
        c->fin_cuerpo = c->quedan == 0;
        return 0;
    }
    return -1;
}

/* Copia del búfer (rellenándolo si hace falta) hasta `n` bytes. */
static int leer_crudo(Conexion *c, char *datos, int n) {
    if (c->buf_ini == c->buf_fin) {
        int r = rellenar(c);
        if (r <= 0) return r;
    }
    int disp = c->buf_fin - c->buf_ini;
    if (n > disp) n = disp;
    memcpy(datos, c->buf + c->buf_ini, (size_t)n);
    c->buf_ini += n;
    return n;
}

int http_leer(Conexion *c, void *datos, int n) {
    if (c->fin_cuerpo) return 0;
    if (c->troceada) {
        if (c->trozo_quedan == 0) {
            char linea[64];
            if (leer_linea(c, linea, sizeof linea)) return -1;
            if (!linea[0] && leer_linea(c, linea, sizeof linea)) return -1;   /* \r\n tras el trozo anterior */
            c->trozo_quedan = strtoll(linea, NULL, 16);
            if (c->trozo_quedan == 0) {
                /* Cabeceras finales (normalmente ninguna) hasta la línea vacía. */
                do {
                    if (leer_linea(c, linea, sizeof linea)) return -1;
                } while (linea[0]);
                c->fin_cuerpo = 1;
                return 0;
            }
        }
        if (n > c->trozo_quedan) n = (int)c->trozo_quedan;
        int r = leer_crudo(c, datos, n);
        if (r <= 0) return -1;
        c->trozo_quedan -= r;
        return r;
    }
    if (c->quedan >= 0 && n > c->quedan) n = (int)c->quedan;
    int r = leer_crudo(c, datos, n);
    if (r == 0 && c->quedan < 0) {
        c->fin_cuerpo = 1;
        c->viva = 0;
        return 0;
    }
    if (r <= 0) return -1;
    if (c->quedan > 0) {
        c->quedan -= r;
        if (c->quedan == 0) c->fin_cuerpo = 1;
    }
    return r;
}

void http_cerrar(Conexion *c) {
    if (!c->fin_cuerpo || c->cerrar_al_final) desconectar(c);
}

int http_memoria(Conexion *c, const char *metodo, const char *url, const char *clave, const char *cuerpo,
                 char **datos, size_t *n, size_t maximo, char *error, size_t nerror) {
    Respuesta r;
    *datos = NULL;
    *n = 0;
    if (http_abrir(c, metodo, url, clave, 0, cuerpo, &r, error, nerror)) return -1;
    if (r.longitud > (long long)maximo) {
        http_cerrar(c);
        snprintf(error, nerror, "Respuesta demasiado grande.");
        return -1;
    }
    size_t cap = r.longitud >= 0 ? (size_t)r.longitud + 1 : 65536, l = 0;
    char *b = malloc(cap);
    for (;;) {
        if (!b) {
            http_cerrar(c);
            snprintf(error, nerror, "Sin memoria.");
            return -1;
        }
        if (l + 1 >= cap) {
            if (cap > maximo) {
                free(b);
                desconectar(c);
                snprintf(error, nerror, "Respuesta demasiado grande.");
                return -1;
            }
            char *nb = realloc(b, cap * 2);
            if (!nb) free(b);
            b = nb;
            cap *= 2;
            continue;
        }
        int leidos = http_leer(c, b + l, (int)(cap - 1 - l > 65536 ? 65536 : cap - 1 - l));
        if (leidos < 0) {
            free(b);
            desconectar(c);
            snprintf(error, nerror, "Se cortó la conexión.");
            return -1;
        }
        if (leidos == 0) break;
        l += (size_t)leidos;
    }
    http_cerrar(c);
    b[l] = 0;
    if (r.estado < 200 || r.estado >= 300) {
        json_t *j = json_loadb(b, l, 0, NULL);
        const char *m = NULL;
        if (j) {
            m = json_string_value(json_object_get(j, "message"));
            if (!m) m = json_string_value(json_object_get(j, "detail"));
        }
        if (m && *m) snprintf(error, nerror, "%s", m);
        else snprintf(error, nerror, "Error %d del servidor.", r.estado);
        json_decref(j);
    }
    *datos = b;
    *n = l;
    return r.estado;
}

void url_codificar(const char *texto, char *salida, size_t n) {
    static const char hex[] = "0123456789ABCDEF";
    size_t j = 0;
    for (const unsigned char *p = (const unsigned char *)texto; *p && j + 4 < n; p++) {
        if (isalnum(*p) || strchr("-_.~", *p)) salida[j++] = (char)*p;
        else {
            salida[j++] = '%';
            salida[j++] = hex[*p >> 4];
            salida[j++] = hex[*p & 15];
        }
    }
    salida[j] = 0;
}
